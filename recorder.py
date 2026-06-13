"""Stable audio recorder using sounddevice + soundfile.

Writes audio directly to disk via callback — no RAM accumulation.
Designed for long, uninterrupted recording sessions.
"""

import os
import threading
import time
from datetime import datetime

import numpy as np
import sounddevice as sd
import soundfile as sf

from audio_io import SAMPLE_RATE


class Recorder:
    """Callback-based audio recorder that streams directly to a WAV file."""

    # SAMPLE_RATE comes from audio_io — the shared speech-pipeline constant.
    CHANNELS = 1           # Mono
    BLOCK_SIZE = 1024      # Frames per callback (~64ms at 16 kHz)

    def __init__(self, output_dir: str | None = None):
        self._output_dir = output_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "VoxNote", "recordings",
        )
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._lock = threading.Lock()

        self._is_recording = False
        self._is_paused = False
        self._start_time: float = 0.0
        self._pause_offset: float = 0.0    # accumulated time before pauses
        self._pause_start: float = 0.0

        self._current_path: str | None = None
        self._peak_level: float = 0.0       # for UI level meter (0.0–1.0)

    # ── Public API ──────────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def file_path(self) -> str | None:
        return self._current_path

    @property
    def peak_level(self) -> float:
        """Current audio peak level (0.0–1.0), updated every callback."""
        return self._peak_level

    @property
    def elapsed(self) -> float:
        """Elapsed recording time in seconds (pauses excluded)."""
        if not self._is_recording:
            return 0.0
        if self._is_paused:
            return self._pause_offset
        return self._pause_offset + (time.monotonic() - self._start_time)

    def start(self, output_dir: str | None = None) -> str:
        """Start recording. Returns the output file path.

        ``output_dir`` overrides the instance default for this recording
        (the caller passes the freshly-resolved recordings dir so a
        mid-session meetings_dir change is honored). The dir is created if
        missing.
        """
        if self._is_recording:
            raise RuntimeError("Already recording")

        target_dir = output_dir or self._output_dir
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._current_path = os.path.join(target_dir, f"recording_{timestamp}.wav")

        self._file = sf.SoundFile(
            self._current_path,
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=self.CHANNELS,
            format="WAV",
            subtype="PCM_16",
        )

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=self.CHANNELS,
            blocksize=self.BLOCK_SIZE,
            dtype="float32",
            callback=self._audio_callback,
        )

        self._is_recording = True
        self._is_paused = False
        self._pause_offset = 0.0
        self._start_time = time.monotonic()
        self._peak_level = 0.0

        self._stream.start()
        return self._current_path

    def pause(self) -> None:
        """Pause recording (audio is not written while paused)."""
        if not self._is_recording or self._is_paused:
            return
        self._is_paused = True
        self._pause_start = time.monotonic()

    def resume(self) -> None:
        """Resume after pause."""
        if not self._is_recording or not self._is_paused:
            return
        self._pause_offset += time.monotonic() - self._pause_start
        self._start_time = time.monotonic()
        self._is_paused = False

    def stop(self) -> str | None:
        """Stop recording and finalize the file. Returns the file path."""
        if not self._is_recording:
            return None

        self._is_recording = False
        self._is_paused = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

        return self._current_path

    def discard(self) -> None:
        """Stop recording and delete the file."""
        path = self.stop()
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        self._current_path = None

    @staticmethod
    def list_devices() -> list[dict]:
        """List available input audio devices."""
        devices = sd.query_devices()
        inputs = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                inputs.append({"index": i, "name": d["name"], "channels": d["max_input_channels"]})
        return inputs

    # ── Callback (runs in audio thread — must be fast) ──────────

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each audio block."""
        if status:
            pass  # Silently ignore xruns — data is still valid

        # Update peak level for UI metering
        peak = np.abs(indata).max()
        self._peak_level = float(peak)

        # Write to disk unless paused
        if not self._is_paused:
            with self._lock:
                if self._file is not None:
                    self._file.write(indata.copy())
