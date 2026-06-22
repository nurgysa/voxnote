import getpass
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

from logging_setup import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a"}

def _default_config_path() -> str:
    """Resolve config.json location.

    Frozen (.exe): ``~/.voxnote/config.json`` — OUTSIDE the bundle so
    a build update never wipes the user's settings (same app-data home as
    directory.json). Source (dev): repo-root config.json
    beside utils.py (unchanged).
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".voxnote", "config.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


_CONFIG_PATH = _default_config_path()


# Pre-VoxNote secret-store dir, kept ONLY as the one-time migration source.
# This is the single place the legacy brand literal may survive (guard-test
# allowlisted). See migrate_legacy_secret_dir().
_LEGACY_SECRET_DIR_NAME = ".audio-transcriber"
_SECRET_DIR_NAME = ".voxnote"


def migrate_legacy_secret_dir() -> None:
    """One-time move of ``~/.audio-transcriber`` → ``~/.voxnote``.

    Keeps existing installs' config.json (API keys), directory.json,
    queue.json, and model cache after the rebrand. Idempotent
    (no-op once ``~/.voxnote`` exists). Best-effort: a failed move is logged
    and swallowed so it can never block startup.

    ORDERING INVARIANT: must run before any code reads config/tokens. Otherwise
    the renamed code reads an empty ``~/.voxnote`` and may overwrite it with
    defaults, orphaning live keys.
    """
    home = os.path.expanduser("~")
    new_dir = os.path.join(home, _SECRET_DIR_NAME)
    old_dir = os.path.join(home, _LEGACY_SECRET_DIR_NAME)
    if os.path.exists(new_dir) or not os.path.isdir(old_dir):
        return
    try:
        shutil.move(old_dir, new_dir)
        logger.info("migrated secret store %s -> %s", old_dir, new_dir)
    except OSError as exc:
        logger.warning("could not migrate %s -> %s: %s", old_dir, new_dir, exc)


def _restrict_posix(path: str) -> bool:
    """chmod a directory to 0o700 (owner-only). False on failure, never raises."""
    try:
        os.chmod(path, 0o700)
        return True
    except OSError as exc:
        logger.warning("could not chmod %s to 0o700: %s", path, exc)
        return False


def _restrict_windows(path: str) -> bool:
    """icacls a directory to the current user only. False on failure, never raises.

    ``/inheritance:r`` drops inherited ACEs (so accounts that would inherit from
    the parent lose access); ``/grant:r`` replaces the user's grant with Full +
    object/container inheritance, making the dir owner-only AND letting existing
    children re-propagate to owner-only while new children inherit it; ``/C``
    continue-on-error; ``/Q`` quiet. CREATE_NO_WINDOW stops a console flashing
    when this runs during a Settings save in the (windowed) GUI build.

    Deliberately NO ``/T``: a real-icacls smoke showed ``/T`` applies the
    ``(OI)(CI)`` inheritance flags to existing FILES (where they are invalid —
    container-only), corrupting their DACL to empty and locking the owner out of
    config.json. The dir-only grant re-propagates owner-only to existing
    children safely (verified: existing config.json -> ``user:(I)(F)``).
    """
    user = os.environ.get("USERNAME") or getpass.getuser()
    cmd = [
        "icacls", path, "/inheritance:r",
        "/grant:r", f"{user}:(OI)(CI)F", "/C", "/Q",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("icacls could not run on %s: %s", path, exc)
        return False
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", "ignore").strip()
        logger.warning("icacls failed on %s (rc=%s): %s", path, result.returncode, stderr)
        return False
    return True


def restrict_dir_to_owner(path: str) -> bool:
    """Best-effort lock directory ``path`` to the current user only (WS-5 P2).

    Defense-in-depth for the secret store ``~/.voxnote`` (config.json
    API keys). POSIX: ``chmod 0o700``. Windows: ``icacls``
    owner-only — the ``os.chmod(0o600)`` the codebase relied on is a silent
    no-op there. Never raises: a failed hardening is logged and the caller
    proceeds (availability > a best-effort ACL). Returns True on success.
    """
    if os.name == "nt":
        return _restrict_windows(path)
    return _restrict_posix(path)


def validate_audio(path: str) -> bool:
    """Check that the file exists and has a supported audio extension."""
    if not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def get_output_path(audio_path: str) -> str:
    """Return the default .md output path next to the audio file.

    Switched from .txt to .md on 2026-05-28 (user request) so the saved
    transcript renders cleanly in Obsidian and other markdown viewers.
    """
    base, _ = os.path.splitext(audio_path)
    return base + ".md"


def save_transcript(text: str, output_path: str) -> None:
    """Write transcript text to a UTF-8 file."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)


def _get_vendored_binary(name: str) -> str | None:
    """Return absolute path to a vendored binary inside the PyInstaller bundle.

    Frozen mode: look in sys._MEIPASS/vendor/ffmpeg/<name>.exe. Returns the
    path if the file exists, None otherwise. Source mode (no sys.frozen):
    always returns None so callers fall through to PATH lookup.
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    candidate = os.path.join(meipass, "vendor", "ffmpeg", f"{name}.exe")
    return candidate if os.path.isfile(candidate) else None


def get_app_icon_path() -> str | None:
    """Return absolute path to the .ico app icon, or None if missing.

    Resolution order:
      1. PyInstaller bundle (frozen mode) — sys._MEIPASS/vendor/icons/voxnote.ico
      2. Repo-root vendor/icons/ — for dev source-mode runs
      3. None — caller skips iconbitmap() rather than crashing on missing file

    Used by ui.app.App.__init__ to set self.iconbitmap() for the window
    title bar (Explorer/Taskbar uses the .exe-embedded icon set via
    voxnote.spec's EXE(icon=...) parameter).
    """
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "vendor", "icons", "voxnote.ico"))
    # Dev source-mode fallback: repo root vendor/icons/ relative to utils.py
    candidates.append(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "vendor", "icons", "voxnote.ico",
    ))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_ffmpeg_path() -> str | None:
    """Return absolute path to ffmpeg, or None if neither bundled nor on PATH.

    Resolution order:
      1. PyInstaller bundle vendor (frozen mode only) — sys._MEIPASS/vendor/ffmpeg/ffmpeg.exe
      2. System PATH — shutil.which("ffmpeg")
      3. None — caller's responsibility to surface a user-friendly error

    audio_io.py uses this in place of bare
    `"ffmpeg"` subprocess args so the cloud-only PyInstaller bundle works
    without ffmpeg on the user's PATH (vendored binaries from gyan.dev
    release-essentials live under vendor/ffmpeg/ — see voxnote.spec).
    """
    vendored = _get_vendored_binary("ffmpeg")
    if vendored:
        return vendored
    return shutil.which("ffmpeg")


def get_ffprobe_path() -> str | None:
    """Mirror of get_ffmpeg_path for ffprobe.

    Currently no production code paths call ffprobe directly (the codebase
    uses ffmpeg's -i input for probing too), but the helper is symmetric
    with get_ffmpeg_path so future code that needs ffprobe metadata reads
    has a one-import home for the resolver.
    """
    vendored = _get_vendored_binary("ffprobe")
    if vendored:
        return vendored
    return shutil.which("ffprobe")


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available (bundled OR on PATH)."""
    return get_ffmpeg_path() is not None


def _seed_default_config(path: str) -> None:
    """Frozen first-run: copy the bundled config.example.json template to
    ``path`` when it is missing, so the live config is fully populated (empty
    keys → first-run banner). No-op in source mode (no sys.frozen / _MEIPASS)."""
    if not getattr(sys, "frozen", False):
        return
    template = os.path.join(getattr(sys, "_MEIPASS", ""), "config.example.json")
    if not os.path.isfile(template):
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copyfile(template, path)


def load_config() -> dict:
    # utf-8-sig (not "utf-8") so a leading UTF-8 BOM is silently stripped on
    # read. Defensive: third-party tooling that touches config.json — Windows
    # Notepad on save, PowerShell 5.1 `Set-Content -Encoding UTF8`, some
    # ZIP-extract pipelines — adds `EF BB BF` at the file start, and the
    # default "utf-8" codec then raises json.JSONDecodeError "Unexpected UTF-8
    # BOM" → silent app-start crash. Verified live on 2026-05-28 when a merge
    # helper script wrote config.json with BOM and the bundle failed to launch.
    #
    # Corruption recovery: a present-but-INVALID config.json (truncated, a
    # crash mid-save, hand-edited) must NOT crash app start. Quarantine the bad
    # file to config.json.corrupt-<ts> and return {} so the app launches and
    # the first-run banner lets the user re-enter keys (the bad file is kept
    # for manual recovery, not silently discarded).
    if not os.path.isfile(_CONFIG_PATH):
        _seed_default_config(_CONFIG_PATH)  # frozen first-run: populate from template
    if os.path.isfile(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _quarantine_corrupt_config(e)
    return {}


def _quarantine_corrupt_config(exc: Exception) -> None:
    """Move a corrupt config.json aside so the next load starts fresh.

    The timestamped backup preserves the bad file for manual recovery instead
    of silently overwriting the user's keys.
    """
    backup = f"{_CONFIG_PATH}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(_CONFIG_PATH, backup)
        logger.warning(
            "config.json is not valid JSON (%s); quarantined to %s — starting "
            "with an empty config", exc, backup,
        )
    except OSError as move_err:
        logger.warning(
            "config.json is corrupt (%s) and could not be quarantined (%s)",
            exc, move_err,
        )


def save_config(config: dict) -> None:
    parent = os.path.dirname(_CONFIG_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
        # WS-5 P2: in frozen mode `parent` is the secret store
        # ~/.voxnote (API keys at rest) — lock it owner-only. In dev
        # mode `parent` is the repo root (config.json beside the code), so skip.
        if getattr(sys, "frozen", False):
            restrict_dir_to_owner(parent)
    # Atomic write: a crash/power-loss mid-write must not leave a half-written
    # config.json (the exact corruption load_config now has to recover from).
    # Write to a sibling tmp, then os.replace (atomic on Windows + POSIX).
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CONFIG_PATH)


# ── Meetings folder — user-configurable, with 3-level fallback ─────────

_DEFAULT_MEETINGS_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "VoxNote", "meetings",
)

# Legacy paths probed on first launch — entries here trigger the
# migration prompt. Kept as a module-level constant so App.__init__
# can pass it to meetings_migration.detect_old_locations.
_LEGACY_HISTORY_LOCATIONS = [
    # Sibling of utils.py — in dev source mode this is <repo>/history/,
    # in PyInstaller bundle it's <bundle>/_internal/history/. Same
    # expression covers both because __file__ resolves differently.
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "history"),
    # PyInstaller bundle "root" (parent of _internal/) — edge case for
    # builds that drop history at bundle root instead of inside _internal.
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "history",
    ),
]


def _normalize_meetings_path(raw: str) -> str:
    """Expand %VARS% / ~, normalize separators, return absolute path."""
    return os.path.abspath(
        os.path.expandvars(os.path.expanduser(raw.strip()))
    )


def get_meetings_dir() -> str:
    """Return absolute path to the active meetings folder, creating it if missing.

    Resolution order (each level falls through on failure):
      1. config["meetings_dir"] if non-empty AND parent exists AND writable
      2. _DEFAULT_MEETINGS_DIR (%USERPROFILE%/Documents/VoxNote/meetings/)
      3. <bundle>/_internal/history/ — legacy last-resort fallback for
         corporate Windows profiles where Documents itself is locked

    The chosen directory is created (mkdir -p) on call. Callers can
    rely on the returned path existing as a directory.
    """
    cfg = load_config()
    candidates: list[str] = []

    raw = (cfg.get("meetings_dir") or "").strip()
    if raw:
        candidates.append(_normalize_meetings_path(raw))
    candidates.append(_DEFAULT_MEETINGS_DIR)
    # Legacy first probe path is the same expression as _LEGACY_HISTORY_LOCATIONS[0]
    candidates.append(_LEGACY_HISTORY_LOCATIONS[0])

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            # Touch-test writability via a temp marker file
            test_marker = os.path.join(path, ".write-test")
            with open(test_marker, "w", encoding="utf-8") as f:
                f.write("")
            os.remove(test_marker)
            return path
        except (OSError, PermissionError):
            continue

    # If everything fails, return the default and let the caller's
    # next os.* operation surface the real error.
    return _DEFAULT_MEETINGS_DIR


def get_recordings_dir() -> str:
    """Directory for raw recordings: ``<meetings_dir>/recordings/``.

    Builds on get_meetings_dir() so it inherits the same 3-level fallback,
    ~/%VAR% expansion, and writability checks — recordings always land as a
    subfolder of whatever meetings dir is actually in use. The subfolder
    itself is created by the write sites (recorder.start, move script), not
    here, so this stays a pure resolver.
    """
    return os.path.join(get_meetings_dir(), "recordings")


def should_delete_after_transcription(config: dict, audio_path: str | None) -> bool:
    """True only when the user opted in AND ``audio_path`` lives inside the
    recordings dir. The path-containment check (not a flag) guarantees a
    user-loaded file from elsewhere is never deleted. Drive-mismatch / bad
    paths fail safe to False (don't delete)."""
    if not config.get("delete_recording_after_transcription", False):
        return False
    if not audio_path:
        return False
    try:
        ap = os.path.normcase(os.path.abspath(audio_path))
        rd = os.path.normcase(os.path.abspath(get_recordings_dir()))
        return os.path.commonpath([ap, rd]) == rd
    except (ValueError, OSError):
        return False  # different drives (Windows) / malformed path → don't delete


def _ensure_history_dir() -> str:
    """Backwards-compat shim — equivalent to get_meetings_dir()."""
    return get_meetings_dir()


def create_history_entry(
    audio_file_path: str,
    transcript_text: str,
    language: str | None,
    model: str,
) -> str:
    """Create a meeting folder with audio copy, transcript.md and description.md.

    Returns the path to the created folder.

    New meetings (2026-05-28+) write transcript.md so Obsidian / markdown
    viewers render the file natively. Pre-existing transcript.txt files
    in older meeting folders remain readable — list_history_entries and
    the meetings dialog _read_transcript helper both accept either
    extension (.md preferred, .txt fallback).
    """
    meetings_dir = get_meetings_dir()

    audio_name = os.path.basename(audio_file_path)
    base_name = os.path.splitext(audio_name)[0]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder_name = f"{timestamp}_{base_name}"
    folder_path = os.path.join(meetings_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # Copy audio file
    if os.path.isfile(audio_file_path):
        shutil.copy2(audio_file_path, os.path.join(folder_path, audio_name))

    # Save transcript as Markdown (plain text content — no actual markdown
    # formatting yet; just the extension that lets viewers render).
    transcript_path = os.path.join(folder_path, "transcript.md")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(transcript_text)

    # Save description.md
    lang_label = language or "auto"
    md_content = (
        f"# {audio_name}\n\n"
        f"- **Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Язык:** {lang_label}\n"
        f"- **Модель:** {model}\n"
        f"- **Аудио файл:** {audio_name}\n"
        f"- **Исходный путь:** {audio_file_path}\n"
    )
    md_path = os.path.join(folder_path, "description.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return folder_path


def save_segments(folder: str, segments: list[dict] | None) -> None:
    """Atomically write raw transcription segments to <folder>/segments.json.

    The audio is copied into the meeting folder by create_history_entry, but the
    per-speaker timestamps would otherwise be lost — they are what later
    speaker-attribution slices on. No-op when segments is None (e.g. a provider
    that returned nothing to cache).
    """
    if segments is None:
        return
    target = os.path.join(folder, "segments.json")
    tmp = os.path.join(folder, ".segments.json.tmp")
    encoded = json.dumps(segments, ensure_ascii=False, indent=2)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(encoded)
        os.replace(tmp, target)
    except OSError as e:
        # Best-effort cache: segments feed later speaker-attribution, but a
        # write failure (disk full, permission) must NOT crash the post-
        # transcription completion handler. Log, clean the tmp, move on.
        logger.warning("could not save %s (%s)", target, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def save_speakers(
    folder: str,
    project_id: str | None,
    participant_ids: list[str],
    speaker_map: dict[str, str] | None = None,
) -> None:
    """Atomically write the meeting's context selection to <folder>/speakers.json.

    ``speaker_map`` is the per-speaker attribution: raw provider label
    (e.g. "SPEAKER_00") → person_id. Defaults to None → writes an empty
    map, preserving backward compatibility with existing callers.
    """
    payload = {
        "project_id": project_id,
        "participants": list(participant_ids),
        "speakers": dict(speaker_map) if speaker_map else {},
    }
    target = os.path.join(folder, "speakers.json")
    tmp = os.path.join(folder, ".speakers.json.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(encoded)
        os.replace(tmp, target)
    except OSError as e:
        # Best-effort context cache — see save_segments. Don't crash the caller.
        logger.warning("could not save %s (%s)", target, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_speakers(folder: str) -> dict:
    """Read <folder>/speakers.json. Returns {} if absent or malformed.

    Never raises — the dialog restore path must degrade silently (a corrupt or
    missing file just means "no remembered selection").
    """
    target = os.path.join(folder, "speakers.json")
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def load_segments(folder: str) -> list[dict]:
    """Read <folder>/segments.json. Returns [] if absent, malformed, or not a list.

    Mirror of load_speakers — the speaker-attribution panel must degrade
    silently when a meeting predates segments.json or the file is corrupt.
    The list guard matters because callers iterate the result (a stray
    object/null from a hand-edit would otherwise crash on seg.get). Never raises.
    """
    target = os.path.join(folder, "segments.json")
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def list_history_entries() -> list[dict]:
    """Scan the meetings directory and return entries sorted by date (newest first).

    Each entry dict: folder_path, folder_name, audio_file, date_created.
    """
    meetings_dir = get_meetings_dir()
    entries = []
    for name in os.listdir(meetings_dir):
        folder_path = os.path.join(meetings_dir, name)
        if not os.path.isdir(folder_path):
            continue

        # Find audio file + check for transcript (either .md new or .txt legacy)
        audio_file = None
        has_transcript = False
        for f in os.listdir(folder_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                audio_file = f
            elif f in ("transcript.md", "transcript.txt"):
                has_transcript = True

        # Parse date from folder name (YYYY-MM-DD_HH-MM-SS_...)
        date_str = name[:19] if len(name) >= 19 else name
        date_display = date_str.replace("_", " ", 1).replace("-", ":", 3)

        entries.append({
            "folder_path": folder_path,
            "folder_name": name,
            "audio_file": audio_file,
            "has_transcript": has_transcript,
            "date_created": date_str,
            "date_display": date_display,
        })

    entries.sort(key=lambda e: e["date_created"], reverse=True)
    return entries


def delete_history_entry(folder_path: str) -> None:
    """Delete a history folder and all its contents."""
    if os.path.isdir(folder_path):
        shutil.rmtree(folder_path)


def open_in_explorer(path: str) -> None:
    """Open a folder in the system file explorer."""
    if os.path.isdir(path):
        os.startfile(path)


def _segments_sidecar_dir() -> str:
    """~/.voxnote/segments — SRT/VTT source data kept OUT of the vault. Home via
    USERPROFILE/HOME so tests can monkeypatch it (mirrors processing/store)."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
    return os.path.join(home, ".voxnote", "segments")


def save_segments_sidecar(
    voxnote_id: str, segments: list[dict], *, base_dir: str | None = None
) -> str:
    """Persist raw segments outside the vault for later SRT/VTT export, keyed by
    the meeting's voxnote_id. Atomic write. Returns the file path."""
    target_dir = base_dir or _segments_sidecar_dir()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{voxnote_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_segments_sidecar(
    voxnote_id: str, *, base_dir: str | None = None
) -> list[dict] | None:
    """Read a sidecar by voxnote_id. None when absent or malformed."""
    target_dir = base_dir or _segments_sidecar_dir()
    path = os.path.join(target_dir, f"{voxnote_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural word form for ``n``: 1 встреча / 2 встречи / 5 встреч.

    Returns the WORD ONLY — callers compose ``f"{n} {plural_ru(...)}"``.
    Handles the 11–14 exception at any hundred (11 встреч, 111 встреч,
    but 21 встреча, 121 встреча).
    """
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many
