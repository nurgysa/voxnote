"""Drive API v3 wrapper for Phase 7.1+ — upload-only surface.

This module is intentionally tiny. It hides the
`googleapiclient.discovery.build` ceremony behind three methods that
Phase 7.1's backup orchestrator (and Phase 7.2's restore) call. Future
phases (scheduler retention cleanup, sync) will extend with `list`,
`delete`, and `download`.

The Drive API client is built lazily on first method call so that
constructing a DriveClient (e.g. at app startup) doesn't pay the
~30-50 MB import + HTTP-discovery cost — only signing-in-and-clicking-
backup does.

Codex P1 lesson from Phase 7.0 PR #39: `googleapiclient.discovery.build`
is imported INSIDE methods, NOT at module top. Tests must patch the
source (`googleapiclient.discovery.build`) — patching
`gdrive.client.build` would AttributeError because the lazy import
never binds `build` as a `gdrive.client` attribute.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# MIME types used by the backup payload. Drive folders have a magic
# MIME; arbitrary application data uses application/octet-stream
# unless we know better (JSON / ZIP get accurate types so Drive's web
# UI can preview them).
FOLDER_MIME = "application/vnd.google-apps.folder"
JSON_MIME = "application/json"
ZIP_MIME = "application/zip"


class DriveClient:
    """Synchronous wrapper over Drive API v3. One instance per backup
    operation (cheap; just holds credentials + lazy-built service)."""

    def __init__(self, credentials) -> None:
        self._credentials = credentials
        self._service = None

    def _get_service(self):
        """Lazy-build (and cache) the googleapiclient discovery client.

        cache_discovery=False suppresses a noisy warning about file-based
        discovery caching — we don't need it for our small operation
        count (1 list + 1-2 creates + 3 uploads per backup).
        """
        if self._service is None:
            # Lazy import — see module docstring + Codex P1 lesson.
            from googleapiclient.discovery import build

            self._service = build(
                "drive", "v3",
                credentials=self._credentials,
                cache_discovery=False,
            )
        return self._service

    def find_folder(self, name: str, parent_id: str | None = None) -> str | None:
        """Return the Drive file ID of the first folder named ``name``
        under ``parent_id`` (root if None). None if no match.

        Folder names on Drive are NOT unique — two folders with the
        same name can coexist. We return the FIRST match (ordered by
        Drive's default — typically creation time). Backup orchestrator
        only ever creates one ``audio-transcriber-backup`` folder so
        collisions are user-induced (they manually created a duplicate)
        and we accept whichever Drive returns.
        """
        # Escape single-quote in name per Drive query syntax (rare in
        # our use case but defensive).
        safe_name = name.replace("'", "\\'")
        q_parts = [
            f"name = '{safe_name}'",
            f"mimeType = '{FOLDER_MIME}'",
            "trashed = false",
        ]
        # Parent constraint: Drive's "root" is a magic folder id always
        # pointing at the user's My Drive root. Without this branch,
        # `parent_id=None` would omit the predicate entirely and match
        # the name ANYWHERE in the user's Drive — including a folder
        # called "audio-transcriber-backup" that was created by some
        # other tool, shared from a colleague, or left over from an
        # earlier backup attempt. That would then become the cached
        # gdrive_root_folder_id, and subsequent backups would attach
        # snapshots to the wrong tree, breaking restore discoverability.
        # Codex caught the contract drift on PR #45.
        parent_target = parent_id if parent_id is not None else "root"
        q_parts.append(f"'{parent_target}' in parents")
        query = " and ".join(q_parts)

        service = self._get_service()
        resp = service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=10,
        ).execute()
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        """Create a Drive folder. Returns the new folder's id.

        Drive API semantics: a "folder" is a file with mimeType
        application/vnd.google-apps.folder. The `parents` field is a
        list (Drive technically supports multiple parents but we never
        use that). Folder names are not unique — caller's job to dedup
        via find_folder first if uniqueness matters.
        """
        body: dict = {
            "name": name,
            "mimeType": FOLDER_MIME,
        }
        if parent_id is not None:
            body["parents"] = [parent_id]

        service = self._get_service()
        resp = service.files().create(body=body, fields="id").execute()
        return resp["id"]

    def find_or_create_folder(self, name: str, parent_id: str | None = None) -> str:
        """find_folder; if None, create_folder. Returns the (existing or
        new) folder id. Used by the orchestrator to ensure the
        ``audio-transcriber-backup`` top folder exists exactly once,
        then create a timestamped child for each snapshot.
        """
        existing = self.find_folder(name, parent_id=parent_id)
        if existing is not None:
            return existing
        return self.create_folder(name, parent_id=parent_id)

    def upload_file(
        self,
        local_path,                   # pathlib.Path or str
        drive_name: str,
        parent_id: str,
        mime_type: str = "application/octet-stream",
    ) -> str:
        """Upload ``local_path`` to Drive under ``parent_id`` with name
        ``drive_name``. Returns the new Drive file id.

        Uses MediaFileUpload (single-shot for files <5 MB, automatically
        resumable above). Phase 7.1's payloads are tiny (~1 MB total
        across manifest + config + history zip), so this is effectively
        a single-shot upload — but the same primitive will scale up
        cleanly if Phase 7.4 (audio opt-in) lands.
        """
        # Lazy import to keep cold-start light. MediaFileUpload lives in
        # googleapiclient.http, not .discovery.
        from googleapiclient.http import MediaFileUpload

        body = {
            "name": drive_name,
            "parents": [parent_id],
        }
        media = MediaFileUpload(str(local_path), mimetype=mime_type)
        service = self._get_service()
        resp = service.files().create(
            body=body,
            media_body=media,
            fields="id",
        ).execute()
        return resp["id"]
