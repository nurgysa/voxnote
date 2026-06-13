"""Tests for scripts/package_release.py — release-bundle safety guards.

``scripts/`` is not an importable package, so load the module by file path
(same pattern as test_ui_constants.py). The guard under test prevents two
silent shipping regressions:
  * the markitdown doc-grounding stack missing because the build venv is
    stale (it shipped that way once — the doc-grounding feature was dead in
    the .exe while requirements.txt advertised it);
  * the bundled ffmpeg GPLv3 license text missing (redistribution compliance).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "package_release",
    Path(__file__).resolve().parent.parent / "scripts" / "package_release.py",
)
package_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(package_release)


def _make_bundle(
    tmp_path, *, markitdown=True, ffmpeg_license=True, scipy=False, pandas_tests=False
):
    """Build a minimal fake PyInstaller onedir bundle under tmp_path.

    ``scipy`` / ``pandas_tests`` simulate the over-broad-collect_all bloat that
    must NOT ship (see _check_no_bloat) — both default off (the lean bundle).
    """
    bundle = tmp_path / "VoxNote"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "VoxNote.exe").write_bytes(b"MZ")
    if markitdown:
        (internal / "markitdown").mkdir()
        (internal / "markitdown" / "__init__.py").write_text("")
    ffmpeg_dir = internal / "vendor" / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True)
    (ffmpeg_dir / "ffmpeg.exe").write_bytes(b"MZ")
    if ffmpeg_license:
        (ffmpeg_dir / "LICENSE.txt").write_text("FFmpeg is licensed under GPLv3...")
    if scipy:
        (internal / "scipy").mkdir()
        (internal / "scipy" / "__init__.py").write_text("")
    if pandas_tests:
        (internal / "pandas" / "tests").mkdir(parents=True)
        (internal / "pandas" / "tests" / "__init__.py").write_text("")
    return bundle


def test_required_assets_pass_when_present(tmp_path):
    """A bundle with markitdown + the ffmpeg license has no violations."""
    bundle = _make_bundle(tmp_path, markitdown=True, ffmpeg_license=True)
    assert package_release._check_required_assets(bundle) == []


def test_required_assets_flag_missing_markitdown(tmp_path):
    """A bundle built from a stale venv (no markitdown) is flagged — this is
    the exact regression that shipped a dead doc-grounding feature."""
    bundle = _make_bundle(tmp_path, markitdown=False, ffmpeg_license=True)
    violations = package_release._check_required_assets(bundle)
    assert any("markitdown" in v.lower() for v in violations), violations


def test_required_assets_flag_missing_ffmpeg_license(tmp_path):
    """A bundle missing the ffmpeg GPLv3 license text is flagged (compliance)."""
    bundle = _make_bundle(tmp_path, markitdown=True, ffmpeg_license=False)
    violations = package_release._check_required_assets(bundle)
    assert any("license" in v.lower() for v in violations), violations


def test_no_bloat_passes_on_lean_bundle(tmp_path):
    """The lean bundle (no scipy, no pandas/tests) has no bloat violations."""
    bundle = _make_bundle(tmp_path)
    assert package_release._check_no_bloat(bundle) == []


def test_no_bloat_flags_scipy(tmp_path):
    """scipy reappearing signals the spec's collect_all over-collection
    regressed — ~76 MB of BLAS the app never imports (it's only pandas'
    optional dep, dropped via excludes=["scipy"])."""
    bundle = _make_bundle(tmp_path, scipy=True)
    violations = package_release._check_no_bloat(bundle)
    assert any("scipy" in v.lower() for v in violations), violations


def test_no_bloat_flags_pandas_tests(tmp_path):
    """A bundled pandas/tests tree signals collect_all("pandas") regressed —
    the entire test suite (~12 MB) the runtime never touches."""
    bundle = _make_bundle(tmp_path, pandas_tests=True)
    violations = package_release._check_no_bloat(bundle)
    assert any("pandas" in v.lower() for v in violations), violations
