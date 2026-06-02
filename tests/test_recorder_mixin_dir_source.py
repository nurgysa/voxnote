import pathlib

_SRC = pathlib.Path("ui/app/recorder_mixin.py").read_text(encoding="utf-8")


def test_start_recording_passes_resolved_recordings_dir():
    start = _SRC.index("def _start_recording(")
    nxt = _SRC.index("def ", start + 1)
    body = _SRC[start:nxt]
    assert "get_recordings_dir()" in body
    assert "output_dir=get_recordings_dir()" in body
