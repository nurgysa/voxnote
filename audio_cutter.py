"""Universal audio cutter with waveform visualization.

Features:
- Visual waveform display with draggable start/end markers
- Precise time input (MM:SS.ms)
- Trim selection and save
- Split audio into multiple parts at marker points
- Audio playback preview

Can run standalone or be launched from the main app.
"""

import os
import subprocess
import threading
import tkinter as tk

import customtkinter as ctk
import numpy as np
import sounddevice as sd

from audio_io import ffmpeg_trim, load_mono_float32
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    MARKER_END_COLOR,
    MARKER_START_COLOR,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WAVE_COLOR,
    WAVE_SELECTED,
    YELLOW,
    t,
)


class AudioCutter(ctk.CTkToplevel):
    """Audio cutter dialog with waveform visualization."""

    def __init__(self, parent=None, audio_path: str | None = None):
        super().__init__(parent)
        self.title("Audio Cutter")
        self.geometry("950x600")
        self.minsize(750, 500)
        self.configure(fg_color=BG)
        if parent:
            self.transient(parent)

        self._audio_path: str | None = None
        self._samples: np.ndarray | None = None
        self._sample_rate: int = 16000
        self._duration: float = 0.0

        # Marker positions in seconds
        self._start_sec: float = 0.0
        self._end_sec: float = 0.0

        # Split points in seconds
        self._split_points: list[float] = []

        # Playback state
        self._is_playing = False
        self._play_stream: sd.OutputStream | None = None

        # Drag state
        self._dragging: str | None = None  # "start", "end", or None

        self._build_ui()

        if audio_path:
            self._load_file(audio_path)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="Audio Cutter",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)

        self._lbl_file = ctk.CTkLabel(
            header, text="Файл не загружен", anchor="e",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_file.grid(row=0, column=1, padx=20, pady=12, sticky="e")

        # --- Controls card ---
        ctrl_card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        ctrl_card.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="ew")
        ctrl_card.grid_columnconfigure(5, weight=1)

        # File button
        ctk.CTkButton(
            ctrl_card, text="Открыть файл", width=140, height=36, corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._open_file,
        ).grid(row=0, column=0, padx=(12, 8), pady=10)

        # Start time
        ctk.CTkLabel(
            ctrl_card, text="Начало:",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=1, padx=(12, 4), pady=10)

        self._start_var = ctk.StringVar(value="00:00.0")
        self._start_entry = ctk.CTkEntry(
            ctrl_card, textvariable=self._start_var, width=90, height=32,
            corner_radius=8, border_color=MARKER_START_COLOR, border_width=2,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._start_entry.grid(row=0, column=2, padx=2, pady=10)
        self._start_entry.bind("<Return>", lambda e: self._apply_time_input())

        # End time
        ctk.CTkLabel(
            ctrl_card, text="Конец:",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=3, padx=(12, 4), pady=10)

        self._end_var = ctk.StringVar(value="00:00.0")
        self._end_entry = ctk.CTkEntry(
            ctrl_card, textvariable=self._end_var, width=90, height=32,
            corner_radius=8, border_color=MARKER_END_COLOR, border_width=2,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._end_entry.grid(row=0, column=4, padx=2, pady=10)
        self._end_entry.bind("<Return>", lambda e: self._apply_time_input())

        # Duration label
        self._lbl_duration = ctk.CTkLabel(
            ctrl_card, text="",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_duration.grid(row=0, column=5, padx=12, pady=10, sticky="w")

        # --- Waveform canvas ---
        wave_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        wave_frame.grid(row=2, column=0, padx=16, pady=4, sticky="nsew")
        wave_frame.grid_columnconfigure(0, weight=1)
        wave_frame.grid_rowconfigure(0, weight=1)

        # tk.Canvas needs a string bg, so resolve the SURFACE tuple via t().
        # Re-applied in _apply_theme() if the user switches theme while the
        # cutter is open.
        self._canvas = tk.Canvas(
            wave_frame, bg=t(SURFACE), highlightthickness=0,
            cursor="crosshair",
        )
        self._canvas.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        # Time axis label
        self._lbl_cursor = ctk.CTkLabel(
            wave_frame, text="",
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_cursor.grid(row=1, column=0, padx=8, pady=(0, 6))

        # Canvas events
        self._canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._canvas.bind("<Motion>", self._on_canvas_motion)
        self._canvas.bind("<Configure>", lambda e: self._draw_waveform())
        self._canvas.bind("<ButtonPress-3>", self._on_right_click)

        # --- Action buttons ---
        btn_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        btn_frame.grid(row=3, column=0, padx=16, pady=(4, 12), sticky="ew")

        # Play
        self._btn_play = ctk.CTkButton(
            btn_frame, text="▶  Слушать", width=130, height=38, corner_radius=19,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            command=self._toggle_play, state="disabled",
        )
        self._btn_play.grid(row=0, column=0, padx=12, pady=10)

        # Cut
        self._btn_cut = ctk.CTkButton(
            btn_frame, text="✂  Обрезать", width=140, height=38, corner_radius=19,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=GREEN, hover_color="#6BAF82", text_color="#1F1F1F",
            command=self._cut_selection, state="disabled",
        )
        self._btn_cut.grid(row=0, column=1, padx=4, pady=10)

        # Split
        self._btn_split = ctk.CTkButton(
            btn_frame, text="⫼  Разделить", width=140, height=38, corner_radius=19,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color="#8AB4F8",
            command=self._split_audio, state="disabled",
        )
        self._btn_split.grid(row=0, column=2, padx=4, pady=10)

        # Add split point
        self._btn_add_split = ctk.CTkButton(
            btn_frame, text="+ Точка разреза", width=140, height=38, corner_radius=19,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT, text_color=YELLOW,
            command=self._add_split_point, state="disabled",
        )
        self._btn_add_split.grid(row=0, column=3, padx=4, pady=10)

        # Clear splits
        self._btn_clear_splits = ctk.CTkButton(
            btn_frame, text="Очистить", width=100, height=38, corner_radius=19,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            command=self._clear_split_points, state="disabled",
        )
        self._btn_clear_splits.grid(row=0, column=4, padx=4, pady=10)

        # Status
        self._lbl_status = ctk.CTkLabel(
            btn_frame, text="",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
        )
        self._lbl_status.grid(row=0, column=5, padx=12, pady=10, sticky="e")
        btn_frame.grid_columnconfigure(5, weight=1)

    # ── File loading ──────────────────────────────────────────

    def _open_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[("Audio files", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("All files", "*.*")],
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        try:
            self._lbl_status.configure(text="Загрузка...", text_color=TEXT_SECONDARY)
            self.update()

            # Unified audio I/O: load_mono_float32 handles WAV directly and
            # routes other formats through ffmpeg. Result is always 1-D mono
            # float32.
            data, sr = load_mono_float32(path)

            self._audio_path = path
            self._samples = data
            self._sample_rate = sr
            self._duration = len(data) / sr

            self._start_sec = 0.0
            self._end_sec = self._duration
            self._split_points.clear()

            self._update_time_labels()
            self._lbl_file.configure(text=os.path.basename(path), text_color=TEXT_PRIMARY)
            self._lbl_duration.configure(text=f"Длительность: {self._fmt(self._duration)}")

            # Enable buttons
            for btn in (self._btn_play, self._btn_cut, self._btn_split,
                        self._btn_add_split, self._btn_clear_splits):
                btn.configure(state="normal")

            self._draw_waveform()
            self._lbl_status.configure(text="Готово", text_color=GREEN)

        except Exception as e:
            self._lbl_status.configure(text=f"Ошибка: {e}", text_color=RED)

    # ── Waveform drawing ──────────────────────────────────────

    def _draw_waveform(self):
        self._canvas.delete("all")
        if self._samples is None:
            return

        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w < 10 or h < 10:
            return

        mid_y = h / 2
        pad = 20

        # Downsample waveform for display
        n_points = min(len(self._samples), w * 2)
        step = max(1, len(self._samples) // n_points)
        display = self._samples[::step]

        # Draw selection background. Light translucent blue tint in light
        # mode, deeper navy in dark mode — both readable as "this is the
        # selected range" without overpowering the waveform itself.
        x_start = self._sec_to_x(self._start_sec, w)
        x_end = self._sec_to_x(self._end_sec, w)
        self._canvas.create_rectangle(
            x_start, 0, x_end, h,
            fill=t(("#D2E3FC", "#1A3A5C")), outline="",
        )

        # Draw split point lines
        for sp in self._split_points:
            x = self._sec_to_x(sp, w)
            self._canvas.create_line(
                x, 0, x, h, fill=t(YELLOW), width=2, dash=(4, 4),
            )

        # Draw waveform
        points = []
        for i, sample in enumerate(display):
            x = (i / len(display)) * w
            y = mid_y - sample * (mid_y - pad)
            points.append((x, y))

        if len(points) > 1:
            wave_in = t(WAVE_SELECTED)
            wave_out = t(WAVE_COLOR)
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i + 1]
                # cur_t — local *time* at this waveform sample. Renamed
                # from `t` to avoid shadowing the imported color resolver.
                cur_t = (i / len(points)) * self._duration
                color = wave_in if self._start_sec <= cur_t <= self._end_sec else wave_out
                self._canvas.create_line(x1, y1, x2, y2, fill=color, width=1)

        # Draw center line
        self._canvas.create_line(0, mid_y, w, mid_y, fill=t(BORDER), width=1)

        # Draw time markers on axis
        interval = self._get_time_interval()
        cur_t = 0.0
        tick_color = t(TEXT_SECONDARY)
        while cur_t <= self._duration:
            x = self._sec_to_x(cur_t, w)
            self._canvas.create_line(x, h - 15, x, h - 5, fill=tick_color, width=1)
            self._canvas.create_text(
                x, h - 2, text=self._fmt(cur_t), fill=tick_color,
                font=(FONT, 9), anchor="s",
            )
            cur_t += interval

        # Draw start marker
        start_color = t(MARKER_START_COLOR)
        self._canvas.create_line(
            x_start, 0, x_start, h,
            fill=start_color, width=3, tags="marker_start",
        )
        self._canvas.create_polygon(
            x_start - 8, 0, x_start + 8, 0, x_start, 12,
            fill=start_color, tags="marker_start",
        )

        # Draw end marker
        end_color = t(MARKER_END_COLOR)
        self._canvas.create_line(
            x_end, 0, x_end, h,
            fill=end_color, width=3, tags="marker_end",
        )
        self._canvas.create_polygon(
            x_end - 8, 0, x_end + 8, 0, x_end, 12,
            fill=end_color, tags="marker_end",
        )

    def _apply_theme(self) -> None:
        """Re-paint the waveform Canvas for the current CTk mode.

        CTk widgets in this dialog (frames, buttons, labels) auto-update
        on ``ctk.set_appearance_mode``; only the plain ``tk.Canvas`` needs
        an explicit color sync. Called by App._on_appearance_changed when
        the user switches theme with the cutter open.
        """
        try:
            self._canvas.config(bg=t(SURFACE))
            self._draw_waveform()
        except Exception:
            pass

    def _sec_to_x(self, sec: float, canvas_width: int) -> float:
        if self._duration <= 0:
            return 0
        return (sec / self._duration) * canvas_width

    def _x_to_sec(self, x: float) -> float:
        w = self._canvas.winfo_width()
        if w <= 0:
            return 0
        sec = (x / w) * self._duration
        return max(0.0, min(sec, self._duration))

    def _get_time_interval(self) -> float:
        if self._duration <= 10:
            return 1.0
        if self._duration <= 60:
            return 5.0
        if self._duration <= 300:
            return 15.0
        if self._duration <= 600:
            return 30.0
        return 60.0

    # ── Canvas interaction ────────────────────────────────────

    def _on_canvas_press(self, event):
        if self._samples is None:
            return
        w = self._canvas.winfo_width()
        x = event.x

        x_start = self._sec_to_x(self._start_sec, w)
        x_end = self._sec_to_x(self._end_sec, w)

        # Check if near a marker (within 10px)
        if abs(x - x_start) < 10:
            self._dragging = "start"
        elif abs(x - x_end) < 10:
            self._dragging = "end"
        else:
            # Click sets start marker, drag will set end
            self._dragging = "end"
            self._start_sec = self._x_to_sec(x)
            self._end_sec = self._start_sec
            self._update_time_labels()
            self._draw_waveform()

    def _on_canvas_drag(self, event):
        if self._dragging is None or self._samples is None:
            return

        sec = self._x_to_sec(event.x)

        if self._dragging == "start":
            self._start_sec = min(sec, self._end_sec)
        elif self._dragging == "end":
            self._end_sec = max(sec, self._start_sec)

        self._update_time_labels()
        self._draw_waveform()

    def _on_canvas_release(self, event):
        if self._dragging == "end" and self._end_sec < self._start_sec:
            self._start_sec, self._end_sec = self._end_sec, self._start_sec
            self._update_time_labels()
            self._draw_waveform()
        self._dragging = None

    def _on_canvas_motion(self, event):
        if self._samples is None:
            return
        sec = self._x_to_sec(event.x)
        self._lbl_cursor.configure(text=f"Курсор: {self._fmt(sec)}")

    def _on_right_click(self, event):
        """Right-click adds a split point at cursor position."""
        if self._samples is None:
            return
        sec = self._x_to_sec(event.x)
        self._split_points.append(sec)
        self._split_points.sort()
        self._draw_waveform()
        self._lbl_status.configure(
            text=f"Точка разреза: {self._fmt(sec)} (всего: {len(self._split_points)})",
            text_color=YELLOW,
        )

    # ── Time helpers ──────────────────────────────────────────

    @staticmethod
    def _fmt(seconds: float) -> str:
        m, s = divmod(seconds, 60)
        h, m = divmod(int(m), 60)
        if h > 0:
            return f"{h}:{int(m):02d}:{s:05.2f}"
        return f"{int(m):02d}:{s:05.2f}"

    @staticmethod
    def _parse_time(text: str) -> float | None:
        """Parse MM:SS.ms or H:MM:SS.ms to seconds."""
        text = text.strip()
        try:
            parts = text.split(":")
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
            elif len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
        except (ValueError, IndexError):
            pass
        return None

    def _update_time_labels(self):
        self._start_var.set(self._fmt(self._start_sec))
        self._end_var.set(self._fmt(self._end_sec))
        sel = self._end_sec - self._start_sec
        if sel > 0:
            self._lbl_duration.configure(
                text=f"Выделено: {self._fmt(sel)}  /  Всего: {self._fmt(self._duration)}",
            )

    def _apply_time_input(self):
        s = self._parse_time(self._start_var.get())
        e = self._parse_time(self._end_var.get())
        if s is not None:
            self._start_sec = max(0.0, min(s, self._duration))
        if e is not None:
            self._end_sec = max(self._start_sec, min(e, self._duration))
        self._update_time_labels()
        self._draw_waveform()

    # ── Playback ──────────────────────────────────────────────

    def _toggle_play(self):
        if self._is_playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if self._samples is None:
            return

        start_idx = int(self._start_sec * self._sample_rate)
        end_idx = int(self._end_sec * self._sample_rate)
        selection = self._samples[start_idx:end_idx]

        if len(selection) == 0:
            return

        self._is_playing = True
        self._btn_play.configure(text="⏹  Стоп")

        def play():
            try:
                sd.play(selection, samplerate=self._sample_rate)
                sd.wait()
            except Exception:
                pass
            finally:
                self.after(0, self._on_playback_done)

        threading.Thread(target=play, daemon=True).start()

    def _stop_playback(self):
        sd.stop()
        self._on_playback_done()

    def _on_playback_done(self):
        self._is_playing = False
        self._btn_play.configure(text="▶  Слушать")

    # ── Cut / Save ────────────────────────────────────────────

    def _cut_selection(self):
        if not self._audio_path or self._start_sec >= self._end_sec:
            return

        from tkinter import filedialog
        base, ext = os.path.splitext(os.path.basename(self._audio_path))
        default_name = f"{base}_cut{ext}"

        path = filedialog.asksaveasfilename(
            title="Сохранить обрезанный файл",
            initialfile=default_name,
            defaultextension=ext,
            filetypes=[
                ("Audio files", "*.mp3 *.wav *.m4a *.flac"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self._lbl_status.configure(text="Обрезка...", text_color=TEXT_SECONDARY)
        self.update()

        try:
            ffmpeg_trim(self._audio_path, self._start_sec, self._end_sec, path)
            self._lbl_status.configure(
                text=f"Сохранено: {os.path.basename(path)}", text_color=GREEN,
            )
        except subprocess.CalledProcessError as e:
            self._lbl_status.configure(text=f"Ошибка: {e}", text_color=RED)

    # ── Split ─────────────────────────────────────────────────

    def _add_split_point(self):
        """Add a split point at the current start marker position."""
        if self._samples is None:
            return
        mid = (self._start_sec + self._end_sec) / 2
        if mid not in self._split_points:
            self._split_points.append(mid)
            self._split_points.sort()
            self._draw_waveform()
            self._lbl_status.configure(
                text=f"Точка разреза: {self._fmt(mid)} (всего: {len(self._split_points)})",
                text_color=YELLOW,
            )

    def _clear_split_points(self):
        self._split_points.clear()
        self._draw_waveform()
        self._lbl_status.configure(text="Точки разреза удалены", text_color=TEXT_SECONDARY)

    def _split_audio(self):
        if not self._audio_path or not self._split_points:
            if not self._split_points:
                from tkinter import messagebox
                messagebox.showinfo(
                    "Нет точек разреза",
                    "Добавьте точки разреза:\n"
                    "• Кнопка «+ Точка разреза» — в середине выделения\n"
                    "• Правый клик на волне — в позиции курсора",
                )
            return

        from tkinter import filedialog
        out_dir = filedialog.askdirectory(title="Выберите папку для частей")
        if not out_dir:
            return

        self._lbl_status.configure(text="Разделение...", text_color=TEXT_SECONDARY)
        self.update()

        base, ext = os.path.splitext(os.path.basename(self._audio_path))
        points = [0.0] + self._split_points + [self._duration]
        errors = []

        for i in range(len(points) - 1):
            start = points[i]
            end = points[i + 1]
            out_path = os.path.join(out_dir, f"{base}_part{i + 1}{ext}")

            try:
                ffmpeg_trim(self._audio_path, start, end, out_path)
            except subprocess.CalledProcessError as e:
                errors.append(f"Part {i + 1}: {e}")

        n = len(points) - 1
        if errors:
            self._lbl_status.configure(
                text=f"Разделено с ошибками: {len(errors)}/{n}", text_color=RED,
            )
        else:
            self._lbl_status.configure(
                text=f"Разделено на {n} частей → {os.path.basename(out_dir)}",
                text_color=GREEN,
            )

def main():
    app = ctk.CTk()
    app.withdraw()
    ctk.set_appearance_mode("dark")
    cutter = AudioCutter(app)
    cutter.protocol("WM_DELETE_WINDOW", lambda: (app.destroy()))
    app.mainloop()


if __name__ == "__main__":
    main()
