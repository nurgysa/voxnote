import pathlib

_SRC = pathlib.Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")


def test_on_complete_deletes_recording_when_helper_says_so():
    start = _SRC.index("def _on_complete(")
    nxt = _SRC.index("def _on_error(")
    body = _SRC[start:nxt]
    assert "should_delete_after_transcription(self._config, self._audio_path)" in body
    assert "os.unlink(" in body or "os.remove(" in body
    # guarded so a delete failure never crashes the success flow
    assert "except OSError" in body
