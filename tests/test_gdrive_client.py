"""Tests for gdrive.client.DriveClient — Phase 7.1.

Pure module — no real Drive API, no network. Mocks
`googleapiclient.discovery.build` at its source module so the
lazily-imported symbol inside DriveClient methods resolves to a
MagicMock that returns canned Drive API responses.

Codex P1 lesson from PR #39 applies: patch the SOURCE
(`googleapiclient.discovery.build`) NOT `gdrive.client.build` —
lazy imports don't bind names as module attributes.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from gdrive.client import FOLDER_MIME, JSON_MIME, DriveClient


def test_constructor_takes_credentials_and_stores_them():
    """DriveClient(creds) stores the credentials object without touching
    the network. The actual `build()` call happens lazily on first API
    method call so construction stays cheap (~µs)."""
    fake_creds = MagicMock()
    client = DriveClient(fake_creds)
    assert client._credentials is fake_creds
    assert client._service is None, "Service should be lazy"


def test_get_service_builds_lazily_and_caches():
    """First call to _get_service() builds the discovery client; second
    call returns the cached instance without rebuilding. Important
    because discovery makes an HTTP GET to /v3/discovery (or hits the
    cached static schema) — we don't want N calls per backup."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service) as mock_build:
        first = client._get_service()
        second = client._get_service()

    assert first is fake_service
    assert second is fake_service
    mock_build.assert_called_once_with("drive", "v3", credentials=fake_creds, cache_discovery=False)


def test_find_folder_returns_id_when_match_exists():
    """find_folder runs files().list with a name + mimeType + parent
    query. Returns the first matching folder's id, or None."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "folder-id-123", "name": "voxnote-backup"}]
    }
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = client.find_folder("voxnote-backup")

    assert result == "folder-id-123"
    # Verify the query was correct (escapes name, filters by folder mime + non-trashed).
    fake_service.files.return_value.list.assert_called_once()
    call_kwargs = fake_service.files.return_value.list.call_args.kwargs
    assert "name = 'voxnote-backup'" in call_kwargs["q"]
    assert FOLDER_MIME in call_kwargs["q"]
    assert "trashed = false" in call_kwargs["q"]


def test_find_folder_returns_none_when_no_match():
    """No folder by that name → None, not exception. Caller decides
    whether to create one."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {"files": []}
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        assert client.find_folder("does-not-exist") is None


def test_find_folder_no_parent_constrains_to_drive_root():
    """Regression for Codex P2 on PR #45: when parent_id is None,
    find_folder must constrain the search to Drive's "root" folder,
    not search the entire Drive.

    Without this constraint, a folder named "voxnote-backup"
    that the user has elsewhere in their Drive (shared from a
    colleague, leftover from another tool, etc.) would shadow the
    real one — causing subsequent backups to attach to the wrong
    tree and breaking restore discoverability.

    The fix: when parent_id is None, the query must include
    "'root' in parents". This pins that behavior so a future refactor
    can't silently drop the predicate.
    """
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {"files": []}
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        client.find_folder("voxnote-backup")

    call_kwargs = fake_service.files.return_value.list.call_args.kwargs
    assert "'root' in parents" in call_kwargs["q"], (
        f"Expected 'root' in parents predicate in query, got: {call_kwargs['q']!r}"
    )


def test_find_folder_with_parent_id_uses_that_parent():
    """When parent_id is given explicitly, the query uses it (NOT 'root').
    Complement to the no-parent regression test: ensures the fix
    doesn't accidentally hardcode 'root' for all calls."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {"files": []}
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        client.find_folder("2026-05-24T13-00-00", parent_id="explicit-parent-id")

    call_kwargs = fake_service.files.return_value.list.call_args.kwargs
    assert "'explicit-parent-id' in parents" in call_kwargs["q"]
    assert "'root' in parents" not in call_kwargs["q"]


def test_create_folder_calls_files_create_with_correct_metadata():
    """create_folder(name, parent_id) calls files().create with the
    folder MIME, the name, and the parent (if given). Returns the new
    folder id from the response."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "newly-created-id"
    }
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = client.create_folder("voxnote-backup")

    assert result == "newly-created-id"
    fake_service.files.return_value.create.assert_called_once()
    body = fake_service.files.return_value.create.call_args.kwargs["body"]
    assert body == {
        "name": "voxnote-backup",
        "mimeType": FOLDER_MIME,
    }


def test_create_folder_with_parent_includes_parents_field():
    """When parent_id is given, the metadata body includes it under
    the `parents` list per Drive API conventions."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "child-id"
    }
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        client.create_folder("2026-05-23T22-00-00", parent_id="root-folder-id")

    body = fake_service.files.return_value.create.call_args.kwargs["body"]
    assert body["parents"] == ["root-folder-id"]


def test_find_or_create_folder_returns_existing_when_match():
    """If find_folder returns an id, find_or_create_folder returns it
    without calling create. Avoids creating duplicate folders on
    repeat backups."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "existing-id", "name": "voxnote-backup"}]
    }
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = client.find_or_create_folder("voxnote-backup")

    assert result == "existing-id"
    fake_service.files.return_value.create.assert_not_called()


def test_find_or_create_folder_creates_when_no_match():
    """If find_folder returns None, find_or_create_folder calls create
    and returns the new id."""
    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {"files": []}
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "freshly-made-id"
    }
    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = client.find_or_create_folder("voxnote-backup")

    assert result == "freshly-made-id"
    fake_service.files.return_value.create.assert_called_once()


def test_upload_file_calls_files_create_with_media_and_metadata(tmp_path):
    """upload_file builds the right metadata body, wraps the local
    file in a MediaFileUpload, and returns the new file id."""
    # Real file on disk so MediaFileUpload's path validation passes.
    local_file = tmp_path / "manifest.json"
    local_file.write_text('{"version": 1}')

    fake_creds = MagicMock()
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "uploaded-file-id"
    }
    fake_media_cls = MagicMock()
    fake_media_instance = MagicMock()
    fake_media_cls.return_value = fake_media_instance

    client = DriveClient(fake_creds)

    with patch("googleapiclient.discovery.build", return_value=fake_service), \
         patch("googleapiclient.http.MediaFileUpload", fake_media_cls):
        result = client.upload_file(
            local_path=local_file,
            drive_name="manifest.json",
            parent_id="snapshot-folder-id",
            mime_type=JSON_MIME,
        )

    assert result == "uploaded-file-id"

    # Body has name + parent (no MIME — Drive infers from MediaFileUpload).
    body = fake_service.files.return_value.create.call_args.kwargs["body"]
    assert body == {
        "name": "manifest.json",
        "parents": ["snapshot-folder-id"],
    }
    # MediaFileUpload was constructed with the local path + mime type.
    fake_media_cls.assert_called_once()
    media_args = fake_media_cls.call_args
    expected_path = str(local_file)
    assert (
        media_args.args[0] == expected_path
        or media_args.kwargs.get("filename") == expected_path
    )
    assert media_args.kwargs.get("mimetype") == JSON_MIME
    # The media kwarg got passed to create().
    create_kwargs = fake_service.files.return_value.create.call_args.kwargs
    assert create_kwargs["media_body"] is fake_media_instance
