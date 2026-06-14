# tests/test_cli_core_speaker_count.py
from cli import core


def test_run_transcribe_forwards_speaker_count(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    captured = {}

    class _FakeTranscriber:
        last_segments = []

        def transcribe(self, audio_path, **kw):
            captured.update(kw)
            return "hi"

    monkeypatch.setattr("transcriber.Transcriber", _FakeTranscriber)
    out = core.run_transcribe(
        str(audio), provider="AssemblyAI", api_key="k",
        num_speakers=3, min_speakers=None, max_speakers=None,
    )
    assert out.text == "hi"
    assert captured["num_speakers"] == 3
    assert captured["min_speakers"] is None
    assert captured["max_speakers"] is None
