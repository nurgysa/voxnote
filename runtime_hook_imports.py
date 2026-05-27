"""PyInstaller runtime hook — CLAUDE.md invariant #1 (faulthandler).

Runs INSIDE the frozen process BEFORE the bundled `app.py` executes, so
native deps that the cloud-only build still pulls (soundfile, sounddevice,
numpy) get faulthandler protection from the very first instruction. The
2026-05-28 rip-out removed the ctranslate2-before-torch DLL-ordering
concern (old invariant #2) — torch and ctranslate2 are no longer in the
bundle — so this hook is now the entire startup-time invariant surface.

PyInstaller wires this via `runtime_hooks=[...]` in the spec (see
audio_transcriber.spec). The hook is part of the bootstrap pre-amble
PyInstaller injects before importing app.py — there is no Python user
code we can run earlier than this.
"""
import faulthandler

faulthandler.enable()
