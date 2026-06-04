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


def _make_bundle(tmp_path, *, markitdown=True, ffmpeg_license=True):
    """Build a minimal fake PyInstaller onedir bundle under tmp_path."""
    bundle = tmp_path / "AudioTranscriber"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "AudioTranscriber.exe").write_bytes(b"MZ")
    if markitdown:
        (internal / "markitdown").mkdir()
        (internal / "markitdown" / "__init__.py").write_text("")
    ffmpeg_dir = internal / "vendor" / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True)
    (ffmpeg_dir / "ffmpeg.exe").write_bytes(b"MZ")
    if ffmpeg_license:
        (ffmpeg_dir / "LICENSE.txt").write_text("FFmpeg is licensed under GPLv3...")
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
