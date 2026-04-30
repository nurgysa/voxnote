"""Real-time system monitor dialog.

Non-modal window showing live CPU / RAM / NVIDIA GPU / network stats.
The user opens this BEFORE or DURING transcription to verify that the
chosen device is actually being engaged (Task Manager on Windows 10
doesn't expose CUDA workloads — see README troubleshooting).

Polling cadence is 1 Hz; psutil/pynvml calls take <10 ms each so the
Tk main loop stays responsive even with the dialog open. All state is
local to this dialog — no callbacks into App, no shared widgets.

NVIDIA section degrades gracefully: if pynvml import fails or no GPU
is found, that section shows "GPU: недоступно" and the rest keeps
ticking. Useful for AMD / Intel / Mac users (when the project ports
that way) and for Windows machines with broken NVML installs.
"""

from __future__ import annotations

import time
import tkinter as tk

import customtkinter as ctk
import psutil

from theme import (
    BG, BLUE, BLUE_DIM, BORDER, FONT, GREEN, RED,
    SURFACE, SURFACE_BRIGHT, TEXT_PRIMARY, TEXT_SECONDARY, t,
)
from ui.widgets import card, label

# pynvml is optional. Importing at module load keeps the failure surface
# small (one ImportError per process, cached) and lets us pick a static
# fallback rendering for non-NVIDIA setups without per-tick try/except.
try:
    import pynvml as _pynvml  # type: ignore[import-untyped]
    _NVML_IMPORT_OK = True
except Exception:
    _pynvml = None  # type: ignore[assignment]
    _NVML_IMPORT_OK = False


# Polling cadence. 1 Hz is a sweet spot:
#   - Low enough that psutil/pynvml overhead is invisible (<1% CPU).
#   - Fast enough to see Whisper chunks (~10-30 s each) and pyannote
#     stages (segmentation/embedding/clustering) move in real time.
_TICK_MS = 1000

# Sparkline geometry. 60 samples × 1000 ms = 60 s of history visible at
# any moment. Width = 240 px (60 bars × 4 px each, no gap). Height tuned
# to fit comfortably under the percentage label in each section.
_SPARK_SAMPLES = 60
_SPARK_BAR_W = 4
_SPARK_W = _SPARK_SAMPLES * _SPARK_BAR_W
_SPARK_H = 36


class _Sparkline:
    """Rolling 60-sample bar chart on a tk.Canvas.

    Cheaper than line plots: each tick we delete one old rect and add a
    new one. Color encodes intensity (green→yellow→red) so a glance is
    enough to spot saturation without reading the number.
    """

    def __init__(self, parent: tk.Misc, max_value: float = 100.0):
        self._max = max_value
        self._values: list[float] = []
        # tk.Canvas needs a string bg — resolve the (light, dark) tuple
        # via t(). Re-applied in _apply_theme on theme switch.
        self._canvas = tk.Canvas(
            parent, width=_SPARK_W, height=_SPARK_H,
            bg=t(SURFACE_BRIGHT), highlightthickness=0,
        )

    def grid(self, **kwargs) -> None:
        self._canvas.grid(**kwargs)

    def push(self, value: float) -> None:
        self._values.append(value)
        if len(self._values) > _SPARK_SAMPLES:
            self._values.pop(0)
        self._redraw()

    def _redraw(self) -> None:
        self._canvas.delete("all")
        # Draw newest sample on the right edge so the chart "scrolls
        # left" as time advances — matches Task Manager's convention.
        n = len(self._values)
        x_off = (_SPARK_SAMPLES - n) * _SPARK_BAR_W
        for i, v in enumerate(self._values):
            ratio = max(0.0, min(1.0, v / self._max if self._max else 0.0))
            h = int(ratio * (_SPARK_H - 2))
            x0 = x_off + i * _SPARK_BAR_W
            color = self._color(ratio)
            self._canvas.create_rectangle(
                x0, _SPARK_H - h, x0 + _SPARK_BAR_W - 1, _SPARK_H,
                fill=color, outline="",
            )

    @staticmethod
    def _color(ratio: float) -> str:
        # Cheap traffic-light gradient. Hard cutoffs (not interpolated)
        # because we want bands the eye can spot without thinking — the
        # exact hue doesn't matter, the *change* does. RED/GREEN are
        # (light, dark) tuples in theme.py — t() resolves to the active
        # mode's hex string so tk.Canvas can use it directly.
        if ratio >= 0.85:
            return t(RED)
        if ratio >= 0.6:
            return "#F4B400"  # Material amber-500 — same in both modes
        return t(GREEN)


class SystemMonitorDialog(ctk.CTkToplevel):
    """Live system stats. Non-modal — runs alongside transcription."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Монитор системы")
        self.geometry("420x520")
        self.configure(fg_color=BG)
        # Intentionally NOT calling transient(parent) / grab_set() —
        # this window must coexist with active transcription. It also
        # stays open if the user clicks back to the main window.

        self.grid_columnconfigure(0, weight=1)

        self._after_id: str | None = None
        self._nvml_handle = None  # NVML device handle, lazy-initialized
        self._nvml_ready = False
        # Auto-recovery state (Phase 6.5+): when _poll_gpu raises (e.g.,
        # torch grabbed CUDA context and stale handle returns
        # NVMLError_Unknown), we set _nvml_recovery_at to a future
        # monotonic timestamp; the next tick at/after that time will
        # call _shutdown_nvml + _init_nvml. Backoff doubles up to a cap
        # so we don't spam re-init on persistently broken systems.
        self._nvml_recovery_at: float | None = None
        self._nvml_recovery_delay = 2.0   # seconds; doubles per failure, capped
        self._NVML_RECOVERY_DELAY_CAP = 30.0

        # psutil's cpu_percent / net_io_counters are cumulative-since-
        # last-call. Initialize the baseline NOW so the very first tick
        # already shows real values instead of zero.
        psutil.cpu_percent(interval=None)
        self._last_net = psutil.net_io_counters()

        self._build_ui()
        self._init_nvml()
        self._tick()  # first refresh now, then every _TICK_MS

        # Cleanup on user-close OR programmatic destroy. Both paths route
        # through _on_close so the timer + NVML handles always release.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------- UI ----------------------------------

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Монитор системы",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=12, pady=8, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        self._gpu_section = self._build_section(body, "GPU (NVIDIA CUDA)", row=0)
        self._cpu_section = self._build_section(body, "CPU", row=1)
        self._ram_section = self._build_section(body, "Память (RAM)", row=2)
        self._net_section = self._build_section(body, "Сеть", row=3)

    def _build_section(self, parent, title: str, row: int) -> dict:
        wrapper = card(parent)
        wrapper.grid(row=row, column=0, padx=4, pady=6, sticky="ew")
        wrapper.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            wrapper, text=title,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 0), sticky="w")

        primary = label(wrapper, "—", size=15)
        primary.grid(row=1, column=0, padx=12, pady=(2, 2), sticky="w")
        secondary = label(wrapper, "", size=11)
        secondary.grid(row=1, column=1, padx=(0, 12), pady=(2, 2), sticky="e")

        spark = _Sparkline(wrapper)
        spark.grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="ew")

        return {"primary": primary, "secondary": secondary, "spark": spark}

    # ------------------------ NVML lifecycle ---------------------------

    def _init_nvml(self) -> None:
        if not _NVML_IMPORT_OK:
            self._gpu_section["primary"].configure(
                text="GPU: pynvml не установлен",
            )
            return
        try:
            _pynvml.nvmlInit()
            count = _pynvml.nvmlDeviceGetCount()
            if count == 0:
                self._gpu_section["primary"].configure(text="GPU: не найден")
                return
            # First device only — multi-GPU laptops are rare in this
            # app's target market (consumer Windows), and showing N
            # cards would crowd the dialog. Easy to extend later.
            self._nvml_handle = _pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_ready = True
        except Exception as e:
            self._gpu_section["primary"].configure(
                text=f"GPU: ошибка NVML ({type(e).__name__})",
            )

    def _shutdown_nvml(self) -> None:
        if not self._nvml_ready:
            return
        try:
            _pynvml.nvmlShutdown()
        except Exception:
            pass
        self._nvml_ready = False
        self._nvml_handle = None

    # ------------------------ Polling ----------------------------------

    def _tick(self) -> None:
        # Wrap each poller individually — a sensor going AWOL (e.g. NVML
        # losing the device on driver restart) shouldn't kill the whole
        # update loop or other sections.

        # NVML auto-recovery: if a previous poll failed and the cooldown
        # has elapsed, try shutdown+init to refresh the device handle.
        # On GTX 1650 Ti / consumer GPUs, torch.cuda.init() during model
        # load can invalidate our cached handle → recovery brings us back.
        if (
            not self._nvml_ready
            and self._nvml_recovery_at is not None
            and time.monotonic() >= self._nvml_recovery_at
        ):
            self._shutdown_nvml()  # clears any half-initialized state
            self._init_nvml()
            if self._nvml_ready:
                # Success — reset backoff for next failure (if any).
                self._nvml_recovery_at = None
                self._nvml_recovery_delay = 2.0
            else:
                # Still broken; schedule another try with longer delay.
                self._schedule_nvml_recovery()

        try:
            self._poll_gpu()
        except Exception as e:
            # First failure (or first after a successful tick) shows
            # «восстановление NVML…», kicks recovery on next eligible tick.
            self._gpu_section["primary"].configure(
                text=f"GPU: восстановление NVML… ({type(e).__name__})",
            )
            self._nvml_ready = False
            if self._nvml_recovery_at is None:
                self._schedule_nvml_recovery()

        try:
            self._poll_cpu()
        except Exception:
            pass
        try:
            self._poll_ram()
        except Exception:
            pass
        try:
            self._poll_net()
        except Exception:
            pass

        self._after_id = self.after(_TICK_MS, self._tick)

    def _schedule_nvml_recovery(self) -> None:
        """Set the next-attempt timestamp using current backoff, then
        double the delay (capped). Called both on first failure and on
        each unsuccessful recovery attempt."""
        self._nvml_recovery_at = time.monotonic() + self._nvml_recovery_delay
        self._nvml_recovery_delay = min(
            self._nvml_recovery_delay * 2.0,
            self._NVML_RECOVERY_DELAY_CAP,
        )

    def _poll_gpu(self) -> None:
        if not self._nvml_ready:
            return
        h = self._nvml_handle
        util = _pynvml.nvmlDeviceGetUtilizationRates(h).gpu  # 0-100
        mem = _pynvml.nvmlDeviceGetMemoryInfo(h)
        used_gb = mem.used / 1024**3
        total_gb = mem.total / 1024**3
        # Temperature can fail on some virtualized GPUs — if so we just
        # skip it and keep showing util/memory.
        try:
            temp = _pynvml.nvmlDeviceGetTemperature(
                h, _pynvml.NVML_TEMPERATURE_GPU,
            )
            temp_str = f"{temp}°C"
        except Exception:
            temp_str = "—"

        self._gpu_section["primary"].configure(
            text=f"{util}%   ·   {used_gb:.1f} / {total_gb:.1f} GB",
        )
        self._gpu_section["secondary"].configure(text=temp_str)
        self._gpu_section["spark"].push(util)

    def _poll_cpu(self) -> None:
        # interval=None ⇒ percent since last call (zero on the very first
        # call, which we already paid for in __init__).
        util = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_count(logical=True)
        self._cpu_section["primary"].configure(text=f"{util:.0f}%")
        self._cpu_section["secondary"].configure(text=f"{cores} ядер")
        self._cpu_section["spark"].push(util)

    def _poll_ram(self) -> None:
        vm = psutil.virtual_memory()
        used_gb = (vm.total - vm.available) / 1024**3
        total_gb = vm.total / 1024**3
        pct = vm.percent
        self._ram_section["primary"].configure(
            text=f"{pct:.0f}%   ·   {used_gb:.1f} / {total_gb:.1f} GB",
        )
        self._ram_section["secondary"].configure(text="")
        self._ram_section["spark"].push(pct)

    def _poll_net(self) -> None:
        # Net is cumulative; we want rate. Diff against last sample,
        # divide by tick interval (in seconds) → bytes/s, then format.
        cur = psutil.net_io_counters()
        dt = _TICK_MS / 1000.0
        down_bps = max(0, (cur.bytes_recv - self._last_net.bytes_recv) / dt)
        up_bps = max(0, (cur.bytes_sent - self._last_net.bytes_sent) / dt)
        self._last_net = cur
        # Sparkline plots total throughput in MB/s with a hard upper
        # bound of ~10 MB/s — covers typical home/office Wi-Fi without
        # rescaling on every tick.
        total_bps = down_bps + up_bps
        self._net_section["primary"].configure(
            text=f"↓ {_fmt_rate(down_bps)}   ↑ {_fmt_rate(up_bps)}",
        )
        self._net_section["secondary"].configure(text="")
        self._net_section["spark"]._max = max(10 * 1024**2, self._net_section["spark"]._max)
        self._net_section["spark"].push(total_bps)

    # ------------------------ Lifecycle --------------------------------

    def _apply_theme(self) -> None:
        """Re-color the four sparkline canvases for the current CTk mode.

        Called by App._on_appearance_changed when the user switches
        theme. CTk widgets (labels, frames) update themselves; Canvas
        widgets are plain Tk and need this manual resync. Bars repaint
        on the next push() with the new RED/GREEN values, so we only
        have to update the canvas background here.
        """
        new_bg = t(SURFACE_BRIGHT)
        for section in (self._gpu_section, self._cpu_section,
                        self._ram_section, self._net_section):
            try:
                section["spark"]._canvas.config(bg=new_bg)
                section["spark"]._redraw()  # repaint with new bar colors
            except Exception:
                pass

    def _on_close(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._shutdown_nvml()
        self.destroy()


def _fmt_rate(bps: float) -> str:
    """Bytes-per-second → human-readable. Matches the convention used
    by Task Manager / nvidia-smi (KB up to 1 MB, then MB)."""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024**2:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps / 1024**2:.1f} MB/s"
