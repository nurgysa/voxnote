from ui.app.queue_mixin import QueueMixin


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _App(QueueMixin):
    def __init__(self, *, diarize, speaker_count="2", provider="AssemblyAI"):
        self._config = {"hotwords": ["VoxNote"]}
        self._cloud_provider_var = _Var(provider)
        self._lang_var = _Var("Русский")
        self._diar_var = _Var(diarize)
        self._spk_count_var = _Var(speaker_count)
        self._denoise_var = _Var(False)
        self._project_var = _Var("Project A")
        self._project_choices = {"Project A": "p1"}


def test_build_options_marks_asr_only_when_diarization_is_off():
    opts = _App(diarize=False)._build_options("pick")

    assert opts["transcription_mode"] == "asr_only"
    assert opts["diarize"] is False
    assert opts["num_speakers"] is None
    assert opts["min_speakers"] is None
    assert opts["max_speakers"] is None


def test_build_options_marks_meeting_mode_when_diarization_is_on():
    opts = _App(diarize=True, speaker_count="2")._build_options("pick")

    assert opts["transcription_mode"] == "meeting"
    assert opts["diarize"] is True
    assert opts["num_speakers"] == 2


def test_build_options_forces_asr_only_for_provider_without_diarization():
    opts = _App(diarize=True, speaker_count="2", provider="Groq")._build_options("pick")

    assert opts["transcription_mode"] == "asr_only"
    assert opts["diarize"] is False
    assert opts["num_speakers"] is None
