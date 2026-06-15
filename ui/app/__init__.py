"""Main App window — file selection, recording, transcription orchestration."""

from __future__ import annotations

import os
import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from directory.store import DirectoryStore
from logging_setup import init_logging, log_callback_exception
from processing.worker import ProcessingQueue
from recorder import Recorder
from theme import BG
from utils import get_app_icon_path, get_meetings_dir, load_config, save_config

# Submodule re-exports. ``main`` lives in ``.main_entry`` so the repo-root
# ``app.py`` (the faulthandler bootstrap) keeps working through its existing
# ``from ui.app import main``. ``main_entry`` imports ``App`` lazily inside
# ``main()``, so this top-level import is safe — no circular load.
from .builder import build_ui
from .constants import (
    APPEARANCE_MODES,
    LANGUAGES,
    MODELS,
    SPEAKER_COUNTS,
    compute_first_run,
)
from .dialogs_mixin import DialogsMixin
from .main_entry import main as main
from .queue_mixin import QueueMixin
from .recorder_mixin import RecorderMixin
from .save_mixin import SaveMixin
from .settings_mixin import SettingsMixin

# Type-only imports — these classes are referenced as type annotations on
# ``App`` attributes (``self._settings_dialog: SettingsDialog | None``, etc.)
# but constructed inside the corresponding mixins. ``from __future__ import
# annotations`` keeps the annotations as strings at runtime, so the imports
# don't need to load unless a type checker is running.
if TYPE_CHECKING:
    from audio_cutter import AudioCutter
    from ui.dialogs.settings import SettingsDialog

init_logging()

__all__ = [
    "APPEARANCE_MODES",
    "App",
    "LANGUAGES",
    "MODELS",
    "SPEAKER_COUNTS",
    "main",
]


def _get_windows_work_area(tk_widget) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the Windows work area — the screen
    minus the taskbar. Used by App.__init__ to size the borderless kiosk
    window so the taskbar doesn't overlap the bottom buttons.

    Raises OSError on non-Windows or if the Win32 call fails — caller
    should fall back to a regular maximized window in that case.
    """
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    # SPI_GETWORKAREA = 0x0030. SystemParametersInfoW reads the work-area
    # rectangle into the RECT we pass via byref. Returns nonzero on success.
    rect = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        0x0030, 0, ctypes.byref(rect), 0,
    )
    if not ok:
        raise OSError("SystemParametersInfo(SPI_GETWORKAREA) failed")

    # winfo_screen* is the fall-back source of screen dimensions if
    # SystemParametersInfo returns a degenerate rect; defensive.
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 200 or height < 200:
        raise OSError(f"work area too small: {width}×{height}")
    return rect.left, rect.top, width, height


class App(
    DialogsMixin,
    RecorderMixin,
    SaveMixin,
    SettingsMixin,
    QueueMixin,
    ctk.CTk,
):
    def __init__(self):
        super().__init__()

        # Route uncaught Tk-callback exceptions to the logger instead of Tk's
        # default stderr print (invisible in a windowed PyInstaller build), so
        # a GUI crash lands in logs/app.log instead of vanishing silently. The
        # "Отправить лог" button (D4) then lets the user ship that log. WS-3.
        self.report_callback_exception = log_callback_exception

        self.title("VoxNote")
        # Geometry will be overwritten by the fullscreen setup below — kept
        # only as the un-fullscreen fallback if the user hits Esc/F11 then
        # the window manager needs a reasonable default size to revert to.
        self.geometry("1280x800")
        self.minsize(960, 680)
        # Set the window title-bar icon. CustomTkinter sets its own default
        # icon during super().__init__() so we must call iconbitmap AFTER.
        # The .exe-embedded icon (Explorer/Taskbar) is set separately via
        # voxnote.spec EXE(icon=...). Silently skip if the .ico
        # file is absent — dev runs without `python scripts/gen_icon.py`
        # shouldn't crash startup.
        _icon_path = get_app_icon_path()
        if _icon_path:
            try:
                self.iconbitmap(_icon_path)
            except tk.TclError:
                # iconbitmap can fail on some WSL/Linux/Wine setups even
                # when the file exists — fall back silently rather than
                # blocking app startup over a cosmetic icon.
                pass

        # Maximize on launch (Windows 'zoomed' state) — fills the work area
        # but KEEPS the title bar and X button visible. Earlier iterations
        # tried overrideredirect+brute-force-geometry for a kiosk feel, but
        # it trapped the maintainer's own session 2026-05-28: borderless
        # windows aren't enumerated in Task Manager's Apps view, leaving
        # zero exit options if the Esc binding didn't fire. Lesson:
        # NEVER hide the title bar without a 100%-verified escape hatch.
        #
        # If state('zoomed') silently fails (intermittent CTk init race
        # seen earlier in the same session), fall back to explicit Win32
        # work-area geometry — but WITHOUT overrideredirect, so the user
        # always retains the OS window controls (X / minimize / drag).
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                work_x, work_y, work_w, work_h = _get_windows_work_area(self)
                self.geometry(f"{work_w}x{work_h}+{work_x}+{work_y}")
            except (tk.TclError, OSError, AttributeError):
                pass

        # F11 toggles maximize — standard chord users expect. No Escape
        # binding because we kept the title bar (X button is the obvious
        # exit; binding Escape there would hijack the key from form fields).
        def _toggle_maximize(_event=None) -> None:
            try:
                if self.state() == "zoomed":
                    self.state("normal")
                else:
                    self.state("zoomed")
            except tk.TclError:
                pass

        self.bind("<F11>", _toggle_maximize)
        # Apply the saved appearance mode BEFORE constructing widgets so
        # tuple colors in theme.py resolve to the right palette on first
        # paint. Default "system" follows the OS setting. Persisted via
        # _on_appearance_changed when the user switches in Settings.
        saved_appearance = load_config().get("appearance_mode", "Тёмная")
        ctk.set_appearance_mode(
            APPEARANCE_MODES.get(saved_appearance, "dark"),
        )
        self.configure(fg_color=BG)

        self._audio_path: str | None = None
        self._recorder = Recorder()
        self._rec_timer_id: str | None = None
        self._config = load_config()
        # One-time migration: collapse the old single ``cloud_api_key``
        # string into a per-provider dict. Lets the user keep separate
        # keys for AssemblyAI/Deepgram/Gladia/etc. without re-entering
        # one when switching the dropdown. Old field is dropped.
        if "cloud_api_keys" not in self._config:
            legacy = self._config.pop("cloud_api_key", "")
            current = self._config.get("cloud_provider", "AssemblyAI")
            self._config["cloud_api_keys"] = (
                {current: legacy} if legacy else {}
            )
            save_config(self._config)
        self._cloud_api_keys: dict[str, str] = (
            self._config["cloud_api_keys"]
        )
        # Open Settings dialog reference (singleton). Lets terms/voices saves
        # refresh its summaries live; None when dialog is closed.
        self._settings_dialog: SettingsDialog | None = None
        # Most recently opened AudioCutter instance. Tracked only so that
        # _on_appearance_changed can ping it to redraw its Canvas — the
        # cutter is otherwise free to be reopened/recreated freely.
        self._cutter: AudioCutter | None = None
        # Path to the most recently loaded meeting folder (set when a meeting
        # is opened from «Встречи» via _load_history_into_main); consumed by
        # _open_extract_tasks_dialog.
        self._last_history_folder: str | None = None

        # First-run detection — builder.py uses this to conditionally render
        # the yellow banner at row=0 prompting the user to enter API keys.
        # Triggers when EITHER mandatory key is missing: AssemblyAI (the MVP
        # default STT provider) OR OpenRouter (needed for task/protocol
        # extraction). Checking only AssemblyAI left a client who set it but
        # not OpenRouter at a silent dead-end on «Извлечь задачи».
        self._first_run = compute_first_run(
            self._cloud_api_keys, self._config.get("openrouter_api_key", ""),
        )

        build_ui(self)

        # Processing queue (PR-C1): record-stop / «Выбрать файл» enqueue here;
        # the serial worker transcribes → vault transcript.md → Drive sources →
        # Hermes nudge. on_change is marshalled to the Tk thread via after(0).
        self._dir_store = DirectoryStore()
        self._dir_store.load()
        self._queue = ProcessingQueue(
            meetings_dir=get_meetings_dir(),
            config_loader=load_config,
            resolve_project=lambda pid: (
                self._dir_store.get_project(pid) if pid else None
            ),
            on_change=self._safe_after_refresh,
        )
        self._queue.start()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._refresh_queue_indicator()

        # First-launch meetings migration check. If meetings_dir isn't
        # explicitly configured AND a legacy history folder still has
        # entries, schedule a one-shot prompt (defer 500 ms so the main
        # window finishes drawing before the modal appears).
        meetings_cfg = (self._config.get("meetings_dir") or "").strip()
        if not meetings_cfg:
            from meetings_migration import detect_old_locations
            from utils import _LEGACY_HISTORY_LOCATIONS
            old_locations = detect_old_locations(
                probe_paths=_LEGACY_HISTORY_LOCATIONS,
            )
            if old_locations:
                # Use the most-populated legacy path as src
                src_path, _src_count = old_locations[0]
                dst_path = get_meetings_dir()
                if os.path.abspath(src_path) != os.path.abspath(dst_path):
                    self.after(500, lambda: self._show_migration_prompt(
                        src_path, dst_path,
                    ))

    def _show_migration_prompt(self, src: str, dst: str) -> None:
        """First-launch migration prompt. 3-button mode."""
        from ui.dialogs.migration import MigrationPromptDialog
        MigrationPromptDialog(
            self, src=src, dst=dst, mode="first_launch",
            on_choice=lambda c: self._on_first_launch_choice(c, src, dst),
        )

    def _on_first_launch_choice(
        self, choice: str, src: str, dst: str,
    ) -> None:
        if choice == "migrate":
            from ui.dialogs.migration import MigrationProgressDialog
            MigrationProgressDialog(
                self, src=src, dst=dst,
                on_done=lambda summary: self._on_first_launch_migrated(
                    summary, dst,
                ),
            )
        elif choice == "keep_old":
            # Point config at the old folder so the user keeps working
            # with the same entries; no files move.
            from utils import save_config
            self._config["meetings_dir"] = src
            save_config(self._config)
        # choice == "later" → do nothing; prompt re-appears next launch

    def _on_first_launch_migrated(
        self, summary: dict, new_path: str,
    ) -> None:
        from utils import save_config
        self._config["meetings_dir"] = new_path
        save_config(self._config)



