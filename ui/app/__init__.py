"""Main App window — file selection, recording, transcription orchestration."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import customtkinter as ctk

from logging_setup import init_logging
from recorder import Recorder
from theme import BG
from utils import load_config, save_config

# Submodule re-exports. ``main`` lives in ``.main_entry`` so the repo-root
# ``app.py`` (the faulthandler bootstrap) keeps working through its existing
# ``from ui.app import main``. ``main_entry`` imports ``App`` lazily inside
# ``main()``, so this top-level import is safe — no circular load.
from .builder import build_ui
from .constants import (
    APPEARANCE_MODES,
    DEVICES,
    LANGUAGES,
    MODELS,
    SPEAKER_COUNTS,
)
from .dialogs_mixin import DialogsMixin
from .main_entry import main as main
from .recorder_mixin import RecorderMixin
from .save_mixin import SaveMixin
from .settings_mixin import SettingsMixin
from .transcription_mixin import TranscriptionMixin

# Type-only imports — these classes are referenced as type annotations on
# ``App`` attributes (``self._settings_dialog: SettingsDialog | None``, etc.)
# but constructed inside the corresponding mixins. ``from __future__ import
# annotations`` keeps the annotations as strings at runtime, so the imports
# don't need to load unless a type checker is running.
if TYPE_CHECKING:
    from audio_cutter import AudioCutter
    from transcriber import Transcriber
    from ui.dialogs.settings import SettingsDialog
    from ui.dialogs.system_monitor import SystemMonitorDialog

init_logging()

__all__ = [
    "APPEARANCE_MODES",
    "App",
    "DEVICES",
    "LANGUAGES",
    "MODELS",
    "SPEAKER_COUNTS",
    "main",
]


class App(
    DialogsMixin,
    RecorderMixin,
    SaveMixin,
    SettingsMixin,
    TranscriptionMixin,
    ctk.CTk,
):
    def __init__(self):
        super().__init__()

        self.title("Audio Transcriber")
        self.geometry("780x700")
        self.minsize(680, 600)
        # Apply the saved appearance mode BEFORE constructing widgets so
        # tuple colors in theme.py resolve to the right palette on first
        # paint. Default "system" follows the OS setting. Persisted via
        # _on_appearance_changed when the user switches in Settings.
        saved_appearance = load_config().get("appearance_mode", "Системная")
        ctk.set_appearance_mode(
            APPEARANCE_MODES.get(saved_appearance, "system"),
        )
        self.configure(fg_color=BG)

        self._audio_path: str | None = None
        self._transcriber: Transcriber | None = None
        self._recorder = Recorder()
        self._is_running = False
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
        # Open System Monitor dialog reference (singleton). Non-modal —
        # designed to stay open during transcription. Re-clicking the
        # button just brings the existing window to the front.
        self._monitor_dialog: SystemMonitorDialog | None = None
        # Most recently opened AudioCutter instance. Tracked only so that
        # _on_appearance_changed can ping it to redraw its Canvas — the
        # cutter is otherwise free to be reopened/recreated freely.
        self._cutter: AudioCutter | None = None
        # Cancel signal for the worker thread. Worker checks this between
        # segments and around the diarization subprocess; setting it
        # interrupts the run within ~250 ms.
        self._cancel_event = threading.Event()
        # Path to the most recent successful transcription's history folder.
        # Populated in _on_complete; consumed by _open_extract_tasks_dialog.
        self._last_history_folder: str | None = None

        # First-run detection — builder.py uses this to conditionally render
        # the yellow banner at row=0 prompting the user to enter API keys.
        # Triggers when the AssemblyAI key is empty after config load.
        # AssemblyAI is the MVP default + only provider that delivers a
        # diarized transcript out of the box; without its key the app
        # can't do its primary job.
        self._first_run = not self._cloud_api_keys.get("AssemblyAI", "").strip()

        build_ui(self)



