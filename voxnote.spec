# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the cloud-only VoxNote Windows bundle.

Build: pyinstaller voxnote.spec --noconfirm
Output: dist/VoxNote/  (onedir bundle, target 150-300 MB)

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

from PyInstaller.utils.hooks import collect_all, copy_metadata

block_cipher = None
PROJECT_ROOT = Path(SPECPATH)
VENDOR_FFMPEG = PROJECT_ROOT / "vendor" / "ffmpeg"
APP_ICON = PROJECT_ROOT / "vendor" / "icons" / "voxnote.ico"

# markitdown document grounding (tasks/doc_context.py — attach reference docs →
# Markdown → LLM context). Two packages genuinely NEED collect_all because
# PyInstaller's static analysis can't see their runtime resources:
#   1. markitdown resolves converters via importlib.metadata ENTRY POINTS →
#      copy_metadata("markitdown") or discovery returns nothing in the .exe.
#   2. Its core dep `magika` (file-type sniffing) ships a compiled ONNX MODEL
#      as package DATA that only collect_all() pulls. (magika runs local CPU
#      ONNX inference — allowed under the amended invariant #2; never GPU.)
# `onnxruntime` and `pandas` do NOT get collect_all: it over-collects
# `onnxruntime.transformers`/`.tools` (which import scipy → ~76 MB of BLAS the
# inference path never touches) and the entire `pandas.tests` suite (~12 MB).
# PyInstaller's OFFICIAL hook-onnxruntime.py / hook-pandas.py collect exactly
# the native runtime + data leanly; we just need each in the import graph, so
# they go in hiddenimports below. magika imports onnxruntime; markitdown's xlsx
# converter imports pandas — both reach the hooks. (Measured: this drops the
# bundle from 568 MB to ~390 MB. See package_release.py size guard.)
_md_datas, _md_binaries, _md_hidden = [], [], []
for _pkg in ("markitdown", "magika"):
    _d, _b, _h = collect_all(_pkg)
    _md_datas += _d
    _md_binaries += _b
    _md_hidden += _h
_md_datas += copy_metadata("markitdown")
_md_hidden += [
    # Lean official-hook deps (see note above) — listed so the hooks fire.
    "pandas", "onnxruntime",
    # markitdown's per-format parsers (static analysis misses these because
    # markitdown loads converters lazily via entry points).
    "pdfminer", "pdfplumber", "PIL", "lxml", "pptx", "openpyxl",
    "mammoth", "markdownify", "bs4", "charset_normalizer", "defusedxml",
]


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
    ] + _md_binaries,
    datas=[
        # Starter config — first-run UX. build_exe.ps1 also copies this
        # as config.json into _internal/ after PyInstaller runs, so
        # utils.load_config() finds a populated file on first launch
        # (avoids the "config not found" cold-start branch in utils.py).
        ("config.example.json", "."),
        # App icon — bundled so utils.get_app_icon_path() resolves it via
        # sys._MEIPASS in frozen mode, and self.iconbitmap() in App.__init__
        # picks it up for the title bar. The .exe Explorer/Taskbar icon is
        # set separately via EXE(icon=...) below.
        (str(APP_ICON), "vendor/icons"),
        # FFmpeg GPLv3 license + written source offer. The vendored ffmpeg is
        # a gyan.dev GPL build, so redistribution requires shipping its license
        # next to the binaries (package_release.py enforces its presence).
        (str(VENDOR_FFMPEG / "LICENSE.txt"), "vendor/ffmpeg"),
        # Aggregate third-party license summary at the bundle root.
        (str(PROJECT_ROOT / "THIRD_PARTY_LICENSES.md"), "."),
    ] + _md_datas,
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
        # Hermes outbound webhook (spec 2026-06-11) — imported only at
        # function level inside processing.worker._process_item;
        # listed explicitly so a frozen build can never miss the package.
        "integrations.hermes.client",
        "integrations.hermes.schema",
    ] + _md_hidden,
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
        # scipy — present ONLY as pandas' OPTIONAL dep (interpolation / sparse /
        # stats). markitdown's xlsx path is pandas.read_excel(engine=openpyxl),
        # which never touches scipy; our code has zero scipy imports; magika
        # uses numpy+onnxruntime. pandas guards its scipy imports, so
        # `import pandas` still succeeds. Excluding drops scipy + scipy.libs
        # (~76 MB of BLAS/LAPACK). Verified by the xlsx doc-grounding smoke.
        "scipy",
        # PyInstaller itself — never needed inside the bundle.
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Trim googleapiclient's bundled API-discovery cache. Its PyInstaller hook
# collects ALL 581 discovery documents — every Google API, ~94 MB — into
# _internal/googleapiclient/discovery_cache/documents/. We only ever build the
# Drive v3 service (gdrive/client.py: build("drive", "v3")), so keep
# drive.v3.json and drop the other 580. build() reads the kept static doc
# offline; were it absent it would fall back to an HTTPS discovery fetch, so
# this is a size-only trim, not a behaviour change.
def _keep_datum(entry):
    dest = entry[0].replace("\\", "/")
    if "/discovery_cache/documents/" in dest:
        return dest.endswith("/drive.v3.json")
    return True


a.datas = [e for e in a.datas if _keep_datum(e)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxNote",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # No console window — GUI app
    icon=str(APP_ICON),  # Windows Explorer + Taskbar + Alt-Tab icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoxNote",
)
