"""Backup orchestrator for Phase 7.1.

Composes `gdrive.client.DriveClient` (Drive I/O) with stdlib zipfile
+ hashlib + json (snapshot building) into a single `run_backup`
entry point that the Settings dialog's worker thread calls when the
user clicks "Сделать backup сейчас".

Four pure helpers:
  * redact_config(cfg)        — strip API keys from a config dict
  * zip_history(src_dir, out) — write history/ to a zip, excluding audio
  * build_manifest(...)       — produce the manifest dict
  * _iso_timestamp()          — folder-name-safe ISO 8601 UTC

Plus the orchestrator:
  * run_backup(auth, config, history_dir, on_status=None) → dict

Pure helpers are unit-testable without DriveClient or network. The
orchestrator is mock-tested with a fake DriveClient.

See spec docs/superpowers/specs/2026-04-30-gdrive-backup-design.md
sections "Backup payload structure (Phase 7.1)" and "API key
redaction in config.json".
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


MANIFEST_VERSION = 1


# String written in place of every redacted secret. Matches the spec's
# "<REDACTED>" literal so the user (or a future restore) can detect
# fields that need re-entry.
REDACTION_PLACEHOLDER = "<REDACTED>"

# Top-level config keys whose values are ALWAYS stripped before upload,
# regardless of their name. This explicit list is the floor; the
# deny-by-default heuristic below (_looks_like_secret) is the ceiling
# that catches anything name-shaped like a secret. Belt and suspenders:
# the Trello keys shipped a cleartext-leak (PR #79) precisely because
# this list was hand-maintained and drifted behind the config schema.
REDACTED_KEYS = (
    "openrouter_api_key",
    "linear_api_key",
    "glide_api_key",
    "assemblyai_api_key",
    "trello_api_key",
    "trello_token",
    "hf_token",
)

# Case-insensitive substrings that mark a top-level string-valued config
# key as a secret. Deny-by-default: a newly-added provider credential is
# redacted automatically as long as its name follows the *_api_key /
# *_token / *_secret / *_password convention the codebase already uses —
# so a future key can't silently leak the way trello_* did.
_SECRET_NAME_HINTS = ("key", "token", "secret", "password")


def _looks_like_secret(key_name: str) -> bool:
    """True if ``key_name`` contains any _SECRET_NAME_HINTS substring."""
    lowered = key_name.lower()
    return any(hint in lowered for hint in _SECRET_NAME_HINTS)


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``config`` with all secret values replaced
    by REDACTION_PLACEHOLDER. Input is never mutated.

    Redaction is deny-by-default for secrets:
      * Any top-level STRING value whose key is in REDACTED_KEYS OR whose
        name looks like a secret (_looks_like_secret) is replaced.
      * cloud_api_keys nested dict — provider names (keys) preserved,
        the actual API keys (values) replaced.

    Non-string values (lists, bools, the cloud_api_keys dict itself) are
    never touched by the string pass, so flags like ``trello_enabled``
    and structures like ``hotwords`` survive intact. Keys absent from the
    input are silently skipped — no spurious placeholder entries appear.
    """
    out = copy.deepcopy(config)
    for key, value in out.items():
        if isinstance(value, str) and (key in REDACTED_KEYS or _looks_like_secret(key)):
            out[key] = REDACTION_PLACEHOLDER
    cloud_keys = out.get("cloud_api_keys")
    if isinstance(cloud_keys, dict):
        out["cloud_api_keys"] = {k: REDACTION_PLACEHOLDER for k in cloud_keys}
    return out


# Audio file extensions excluded from the history.zip — text-only
# backup is the spec's default; audio opt-in is a Phase 7.4 follow-up.
AUDIO_EXTS = (".wav", ".mp3", ".m4a")


def zip_history(src_dir, out_zip) -> None:
    """Zip the contents of ``src_dir`` into ``out_zip``, with two rules:

      * Audio files (AUDIO_EXTS) are SKIPPED — they're typically 50-100
        MB per meeting and the Free Drive tier is 15 GB. Text-only is
        the spec's chosen scope.
      * Relative paths inside the zip are rooted at ``src_dir`` (so a
        file at ``history/2026-05-23_meeting/transcript.txt`` lands in
        the zip as ``2026-05-23_meeting/transcript.txt``).

    Empty source directory produces a valid empty zip (not an error).
    Existing out_zip is overwritten.

    src_dir and out_zip accept str or pathlib.Path.
    """
    # Import zipfile/pathlib lazily — both are stdlib and cheap, but
    # keeping the module-top imports minimal helps grep-ability.
    import zipfile
    from pathlib import Path

    src = Path(src_dir)
    out = Path(out_zip)

    # ZIP_DEFLATED gives ~70% compression on transcript JSON/TXT; small
    # enough payloads that compresslevel default (6) is the right pick
    # — going to 9 saves <1% and costs noticeable CPU.
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            if path.suffix.lower() in AUDIO_EXTS:
                continue
            arcname = path.relative_to(src).as_posix()
            zf.write(path, arcname=arcname)


def _iso_timestamp() -> str:
    """Current UTC time formatted as the spec's folder-name (line 86):
    ``2026-04-30T12-30-00``. The two ``:`` separators inside the time
    portion are replaced with ``-`` for Windows-filename safety (the
    folder is created in Drive but the same string appears in local
    paths during Phase 7.2 restore extraction).
    """
    now = datetime.now(timezone.utc)
    # isoformat() with timespec='seconds' → '2026-04-30T12:30:00+00:00'
    # We want '2026-04-30T12-30-00' — strip tz, replace colons.
    raw = now.strftime("%Y-%m-%dT%H:%M:%S")
    return raw.replace(":", "-")


def build_manifest(
    *,
    files: dict[str, Any],
    transcripts_count: int,
    app_version: str,
    host: str,
    created_at: str,
    audio_included: bool = False,
) -> dict[str, Any]:
    """Build the manifest dict that ships alongside the payload files.

    Schema per spec line 94-108. SHA-256 + byte-size are computed
    per file by streaming (chunked reads — works for arbitrarily
    large payloads, though Phase 7.1's are tiny).

    audio_included defaults to False (text-only is Phase 7.1's scope);
    Phase 7.4's audio opt-in passes True.
    """
    import hashlib
    from pathlib import Path

    files_meta = {}
    for arcname, local in files.items():
        path = Path(local)
        sha = hashlib.sha256()
        size = 0
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                sha.update(chunk)
                size += len(chunk)
        files_meta[arcname] = {"size": size, "sha256": sha.hexdigest()}

    return {
        "version": MANIFEST_VERSION,
        "created_at": created_at,
        "app_version": app_version,
        "host": host,
        "files": files_meta,
        "transcripts_count": transcripts_count,
        "audio_included": audio_included,
    }


# DriveClient is imported lazily inside run_backup so importing
# `gdrive.backup` from anywhere (e.g. Settings dialog at import time)
# doesn't drag in googleapiclient. The test fixture patches
# `gdrive.backup.DriveClient` — that name binding is created by the
# `from .client import DriveClient` line INSIDE run_backup, which
# happens at first call. To make the patch work, we declare the
# binding at module scope via the lazy-loader trick: `DriveClient`
# is a sentinel module-level None; run_backup overwrites it on first
# entry. Tests `patch("gdrive.backup.DriveClient", ...)` after the
# first import-time pass — they intercept the SAME module attribute.
DriveClient = None   # populated lazily on first run_backup() call


def run_backup(
    *,
    auth,
    config: dict[str, Any],
    history_dir,
    work_dir,
    app_version: str = "phase-7.1",
    on_status=None,
) -> dict[str, Any]:
    """Run a complete backup: zip history, redact config, upload all
    three payload files to a fresh timestamped folder on Drive.

    Args:
        auth: `gdrive.auth.GDriveAuth` instance (signed in).
        config: in-memory app config dict — NOT mutated.
        history_dir: pathlib.Path or str — the local history/ folder.
        work_dir: temp scratch dir for staging files. Created if missing and
            ALWAYS removed before returning (success OR failure) — it holds the
            history zip (all transcripts) + config and must not linger in %TEMP%.
        app_version: free-form version string written to manifest.
        on_status: optional callable(str) for progress updates. Called
            with Russian-language phase strings like
            "Создаю архив истории...", "Загружаю manifest.json...",
            etc. Settings dialog's worker uses this to update the
            status badge.

    Returns:
        Dict with root_folder_id, snapshot_folder_id, snapshot_name,
        and uploaded (mapping arcname → Drive file id). Caller
        persists root_folder_id to config so subsequent backups
        skip the find/create-top-folder dance.

    Raises:
        Any googleapiclient error (network, auth, quota) propagates
        unchanged. RefreshError specifically: ensure_valid_credentials
        re-raises it after sign_out, so the caller's status badge
        can prompt re-login.
    """
    import json
    import shutil
    import socket
    from pathlib import Path

    # Lazy import — see module-scope DriveClient sentinel comment.
    global DriveClient
    if DriveClient is None:
        from .client import DriveClient as _DriveClient
        DriveClient = _DriveClient

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    history_path = Path(history_dir)

    def _say(msg: str) -> None:
        logger.info("backup: %s", msg)
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                # on_status is a UI callback; we never let it crash
                # the backup. The status label not updating is a
                # cosmetic issue, not a data-integrity one.
                logger.exception("on_status callback failed (ignored)")

    try:
        # 1. Validate auth — surfaces RefreshError early so we don't waste
        #    time zipping if the user has revoked access in Google account
        #    settings.
        _say("Проверяю авторизацию Google Drive...")
        auth.ensure_valid_credentials()
        credentials = auth.get_credentials()

        # 2. Stage history.zip
        _say("Создаю архив истории...")
        history_zip = work / "history.zip"
        zip_history(history_path, history_zip)

        # 3. Stage redacted config.json (utf-8 — the redacted config carries
        #    Russian UI strings; the platform-default codec mangles them on a
        #    non-UTF-8 Windows locale).
        _say("Готовлю конфиг (API ключи удалены)...")
        redacted_cfg = redact_config(config)
        config_path = work / "config.json"
        config_path.write_text(
            json.dumps(redacted_cfg, indent=2, ensure_ascii=False), encoding="utf-8",
        )

        # 4. Build + stage manifest.json
        _say("Считаю контрольные суммы...")
        snapshot_name = _iso_timestamp()
        manifest = build_manifest(
            files={
                "config.json": config_path,
                "history.zip": history_zip,
            },
            transcripts_count=_count_history_subdirs(history_path),
            app_version=app_version,
            host=socket.gethostname(),
            created_at=snapshot_name,
            audio_included=False,
        )
        manifest_path = work / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
        )

        # 5. Drive: find/create root + create snapshot folder
        client = DriveClient(credentials)
        _say("Подключаюсь к Google Drive...")
        root_id = client.find_or_create_folder("voxnote-backup")
        _say(f"Создаю snapshot {snapshot_name}...")
        snapshot_id = client.create_folder(snapshot_name, parent_id=root_id)

        # 6. Upload three files in deterministic order. Manifest first so
        #    a partial-failure observer can see what should have been
        #    uploaded (Phase 7.2 restore reads manifest.json first).
        uploaded = {}
        # Imports local to keep module-top minimal.
        from .client import JSON_MIME, ZIP_MIME

        for arcname, local, mime in (
            ("manifest.json", manifest_path, JSON_MIME),
            ("config.json", config_path, JSON_MIME),
            ("history.zip", history_zip, ZIP_MIME),
        ):
            _say(f"Загружаю {arcname}...")
            file_id = client.upload_file(
                local_path=local,
                drive_name=arcname,
                parent_id=snapshot_id,
                mime_type=mime,
            )
            uploaded[arcname] = file_id

        _say("✓ Backup готов")

        return {
            "root_folder_id": root_id,
            "snapshot_folder_id": snapshot_id,
            "snapshot_name": snapshot_name,
            "uploaded": uploaded,
        }
    finally:
        # ALWAYS clean the staging dir — it holds history.zip (all meeting
        # transcripts) + the config + manifest. Leaving it on FAILURE
        # accumulated transcripts/PII in %TEMP% across retries (audit P2).
        # Best-effort: a cleanup failure must not mask the original error.
        try:
            shutil.rmtree(work)
        except OSError as e:
            logger.warning("Could not clean up work dir %s: %s", work, e)


def _count_history_subdirs(history_dir) -> int:
    """Count immediate subdirectories of history/ — each one is a
    transcribed meeting. Used for the manifest's transcripts_count
    field (informational; restore UI shows it before downloading)."""
    from pathlib import Path
    p = Path(history_dir)
    if not p.exists():
        return 0
    return sum(1 for child in p.iterdir() if child.is_dir())
