# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the cloud-only Audio Transcriber Windows bundle.

Build: pyinstaller audio_transcriber.spec --noconfirm
Output: dist/AudioTranscriber/  (onedir bundle, target 150-300 MB)

Cloud-only since the 2026-05-28 rip-out: no torch / ctranslate2 /
faster_whisper / pyannote / speechbrain in the build venv (they're gone
from requirements.txt entirely), so PyInstaller never sees them — no
need for the elaborate excludes list v4 of the plan called for.

Bundles:
  - app.py + transcriber/* + providers/* + tasks/* + gdrive/* + ui/* + utils + audio_io
  - vendored ffmpeg.exe + ffprobe.exe (under _internal/vendor/ffmpeg/ at runtime)
  - config.example.json (copied to _internal/config.json by build_exe.ps1
    AFTER PyInstaller runs — see step 5 of the build script for the
    bootstrap-vs-seeded-config rationale)
  - CustomTkinter theme assets (auto-discovered via the customtkinter
    package's data files — PyInstaller's default `--collect-data` for
    package metadata catches them)
"""
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH)
VENDOR_FFMPEG = PROJECT_ROOT / "vendor" / "ffmpeg"


a = Analysis(
    ["app.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        # Vendored ffmpeg + ffprobe — utils.get_ffmpeg_path() (Task 7)
        # will resolve these at runtime via sys._MEIPASS in frozen mode.
        # The plan's Task 7 adds the resolver; until then, callers fall
        # back to `shutil.which("ffmpeg")` which finds nothing in the
        # bundle. Vendor binaries are present here so Task 7 has a
        # target to resolve to.
        (str(VENDOR_FFMPEG / "ffmpeg.exe"), "vendor/ffmpeg"),
        (str(VENDOR_FFMPEG / "ffprobe.exe"), "vendor/ffmpeg"),
    ],
    datas=[
        # Starter config — first-run UX. build_exe.ps1 also copies this
        # as config.json into _internal/ after PyInstaller runs, so
        # utils.load_config() finds a populated file on first launch
        # (avoids the "config not found" cold-start branch in utils.py).
        ("config.example.json", "."),
    ],
    hiddenimports=[
        # Network / HTTP layer
        "requests",
        "urllib3",
        # UI
        "customtkinter",
        # Cloud STT providers — explicit so the registry resolves on
        # first import (providers/__init__.py eagerly imports each).
        "providers.assemblyai",
        "providers.deepgram",
        "providers.gladia",
        "providers.speechmatics",
        # Google Drive backup (Phase 7.0/7.1) — googleapiclient has
        # discovery sub-modules that PyInstaller's static analysis
        # can miss because they're loaded by name at runtime.
        "googleapiclient.discovery",
        "googleapiclient.discovery_cache",
        "googleapiclient.discovery_cache.file_cache",
        "google_auth_oauthlib.flow",
    ],
    hookspath=[],
    runtime_hooks=[str(PROJECT_ROOT / "runtime_hook_imports.py")],
    excludes=[
        # Test deps — not needed at runtime, just bloat.
        "pytest",
        "_pytest",
        "pluggy",
        # Dev tools / debuggers / notebook stack.
        "matplotlib",
        "tkinter.test",
        "unittest",
        "IPython",
        "jupyter",
        # PyInstaller itself — never needed inside the bundle.
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # No console window — GUI app
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AudioTranscriber",
)
