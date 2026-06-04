"""Tests for utils.get_ffmpeg_path / get_ffprobe_path / check_ffmpeg (Task 7).

The helpers resolve to vendor/ffmpeg/<name>.exe inside the PyInstaller
bundle (`sys.frozen` + `sys._MEIPASS`), else fall back to `shutil.which`.
Used by audio_io.py so the cloud-only bundle works without ffmpeg on the
user's PATH.
"""
import sys

from utils import check_ffmpeg, get_ffmpeg_path, get_ffprobe_path


def test_get_ffmpeg_from_path_when_not_frozen(monkeypatch):
    """Source mode (no sys.frozen): defer to shutil.which."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        "utils.shutil.which",
        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
    )
    assert get_ffmpeg_path() == "/usr/bin/ffmpeg"


def test_get_ffmpeg_from_vendor_when_frozen(tmp_path, monkeypatch):
    """Frozen mode: resolve to sys._MEIPASS/vendor/ffmpeg/ffmpeg.exe."""
    fake_bundle = tmp_path / "AudioTranscriber_internal"
    vendor = fake_bundle / "vendor" / "ffmpeg"
    vendor.mkdir(parents=True)
    (vendor / "ffmpeg.exe").write_bytes(b"fake-ffmpeg-binary")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)

    out = get_ffmpeg_path()
    assert out is not None
    assert out.endswith("ffmpeg.exe")
    assert "vendor" in out
    # Path actually points at the file we created
    assert out.replace("\\", "/").endswith("vendor/ffmpeg/ffmpeg.exe")


def test_get_ffmpeg_returns_none_when_neither_exists(monkeypatch):
    """No bundle + nothing on PATH → None (caller responsibility to handle)."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("utils.shutil.which", lambda name: None)
    assert get_ffmpeg_path() is None


def test_get_ffprobe_mirrors_ffmpeg(monkeypatch):
    """ffprobe resolves the same way as ffmpeg (same model, separate binary)."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        "utils.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    assert get_ffprobe_path() == "/usr/bin/ffprobe"


def test_check_ffmpeg_true_via_bundled(tmp_path, monkeypatch):
    """check_ffmpeg() returns True when bundled binary present."""
    fake_bundle = tmp_path / "AudioTranscriber_internal"
    vendor = fake_bundle / "vendor" / "ffmpeg"
    vendor.mkdir(parents=True)
    (vendor / "ffmpeg.exe").write_bytes(b"fake")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)

    assert check_ffmpeg() is True


def test_check_ffmpeg_false_when_missing(monkeypatch):
    """check_ffmpeg() returns False when neither bundle nor PATH has ffmpeg."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("utils.shutil.which", lambda name: None)
    assert check_ffmpeg() is False
