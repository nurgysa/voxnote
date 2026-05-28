import json
import os
import shutil
import sys
from datetime import datetime

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a"}

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def validate_audio(path: str) -> bool:
    """Check that the file exists and has a supported audio extension."""
    if not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def get_output_path(audio_path: str) -> str:
    """Return the default .txt output path next to the audio file."""
    base, _ = os.path.splitext(audio_path)
    return base + ".txt"


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
      1. PyInstaller bundle (frozen mode) — sys._MEIPASS/vendor/icons/audio_transcriber.ico
      2. Repo-root vendor/icons/ — for dev source-mode runs
      3. None — caller skips iconbitmap() rather than crashing on missing file

    Used by ui.app.App.__init__ to set self.iconbitmap() for the window
    title bar (Explorer/Taskbar uses the .exe-embedded icon set via
    audio_transcriber.spec's EXE(icon=...) parameter).
    """
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "vendor", "icons", "audio_transcriber.ico"))
    # Dev source-mode fallback: repo root vendor/icons/ relative to utils.py
    candidates.append(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "vendor", "icons", "audio_transcriber.ico",
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

    audio_io.py and transcriber/cloud_chunker.py use this in place of bare
    `"ffmpeg"` subprocess args so the cloud-only PyInstaller bundle works
    without ffmpeg on the user's PATH (vendored binaries from gyan.dev
    release-essentials live under vendor/ffmpeg/ — see audio_transcriber.spec).
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


def load_config() -> dict:
    # utf-8-sig (not "utf-8") so a leading UTF-8 BOM is silently stripped on
    # read. Defensive: third-party tooling that touches config.json — Windows
    # Notepad on save, PowerShell 5.1 `Set-Content -Encoding UTF8`, some
    # ZIP-extract pipelines — adds `EF BB BF` at the file start, and the
    # default "utf-8" codec then raises json.JSONDecodeError "Unexpected UTF-8
    # BOM" → silent app-start crash. Verified live on 2026-05-28 when a merge
    # helper script wrote config.json with BOM and the bundle failed to launch.
    if os.path.isfile(_CONFIG_PATH):
        with open(_CONFIG_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def save_config(config: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ── Meetings folder — user-configurable, with 3-level fallback ─────────

_DEFAULT_MEETINGS_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "AudioTranscriber", "meetings",
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
      2. _DEFAULT_MEETINGS_DIR (%USERPROFILE%/Documents/AudioTranscriber/meetings/)
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
            with open(test_marker, "w") as f:
                f.write("")
            os.remove(test_marker)
            return path
        except (OSError, PermissionError):
            continue

    # If everything fails, return the default and let the caller's
    # next os.* operation surface the real error.
    return _DEFAULT_MEETINGS_DIR


def _ensure_history_dir() -> str:
    """Backwards-compat shim — equivalent to get_meetings_dir()."""
    return get_meetings_dir()


def create_history_entry(
    audio_file_path: str,
    transcript_text: str,
    language: str | None,
    model: str,
) -> str:
    """Create a meeting folder with audio copy, transcript.txt and description.md.

    Returns the path to the created folder.
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

    # Save transcript
    txt_path = os.path.join(folder_path, "transcript.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
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

        # Find audio file (not .txt, not .md)
        audio_file = None
        has_transcript = False
        for f in os.listdir(folder_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                audio_file = f
            elif f == "transcript.txt":
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
