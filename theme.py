"""Shared CustomTkinter theme constants — light + dark palettes.

Single source of truth for colors and fonts. Each color is a
``(light, dark)`` tuple — CustomTkinter widgets accept this form
natively and pick the right value based on ``ctk.get_appearance_mode()``,
so widget colors live-update on theme change with zero code in the
caller.

Plain ``tk.Canvas`` (waveform in audio_cutter, sparkline in
system_monitor) does not understand tuples — pass colors through
``t(color)`` to resolve to the current mode's hex string. Canvas
widgets that need to react to theme changes must redraw themselves
on ``_apply_theme()``; the App orchestrates this.

Palette is Google Material Design — Dark scheme (matches Whisper UI
heritage) and Light scheme (matches default Material Light).
"""

from __future__ import annotations

import customtkinter as ctk

# ────────────────────────── colors (light, dark) ─────────────────────────
# Keep semantic names. Ordering: light value first, dark second — this is
# the convention CustomTkinter uses everywhere (`fg_color=("#fff","#000")`).

BG = ("#FAFAFA", "#1F1F1F")
SURFACE = ("#FFFFFF", "#282828")
SURFACE_BRIGHT = ("#F1F3F4", "#303030")
BORDER = ("#DADCE0", "#3C4043")
TEXT_PRIMARY = ("#202124", "#E8EAED")
TEXT_SECONDARY = ("#5F6368", "#9AA0A6")

# Brand blue: same hex in both modes — Material's Blue 600 has enough
# contrast on both light and dark backgrounds. Hover-dim variants follow.
BLUE = "#1A73E8"
BLUE_DIM = "#1557B0"
# Tonal-button background. Light: pale blue tint (#E8F0FE = Material Blue 50).
# Dark: muted navy. The hover/pressed state in tonal buttons swaps to
# SURFACE_BRIGHT (defined below), which also varies by mode.
BLUE_SURFACE = ("#E8F0FE", "#2D3B4E")

# Status colors. Darker shades on light (better contrast on white),
# lighter shades on dark (better contrast on near-black).
GREEN = ("#137333", "#81C995")
RED = ("#C5221F", "#F28B82")
YELLOW = ("#F29900", "#FDD663")
# First-run banner text — fixed dark in BOTH modes because YELLOW resolves
# to a bright color in both light and dark (Material's amber/canary).
# TEXT_PRIMARY's near-white dark-mode value would have unreadable contrast
# on the bright yellow.
BANNER_TEXT_ON_YELLOW = ("#202124", "#202124")

PROGRESS_BG = ("#E8EAED", "#3C4043")
INPUT_BG = ("#FFFFFF", "#303030")

FONT = "Segoe UI"

# ───────────── audio_cutter waveform / system_monitor sparklines ────────
# Same dual-tuple form. Resolved via t() at draw time on tk.Canvas.
WAVE_COLOR = ("#1A73E8", "#5E97D0")
WAVE_SELECTED = ("#1A73E8", "#8AB4F8")
MARKER_START_COLOR = ("#137333", "#81C995")
MARKER_END_COLOR = ("#C5221F", "#F28B82")
SELECTION_COLOR = "#1A73E8"   # same in both — selection accent stays


# ────────────────────────── Canvas resolver ──────────────────────────────


def t(color):
    """Resolve a (light, dark) tuple to a hex string by current CTk mode.

    Use for ``tk.Canvas`` and any other plain-Tk widget that doesn't
    accept tuple colors. CTk widgets should pass tuples directly —
    they handle the live-switch automatically.

    Plain strings pass through unchanged, so call sites can mix
    theme-aware colors and fixed colors freely::

        canvas.create_rectangle(..., fill=t(BG), outline=t(BORDER))
        canvas.create_line(..., fill=BLUE)  # BLUE is already a string
    """
    if not isinstance(color, tuple):
        return color
    # ctk.get_appearance_mode() returns "Light" | "Dark" (capitalised).
    # On "System" mode CTk resolves to whichever the OS reports here.
    mode = ctk.get_appearance_mode()
    return color[1] if mode == "Dark" else color[0]
