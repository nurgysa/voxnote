from providers import PROVIDERS, get_provider
from providers.groq import GroqProvider


def test_registry_includes_groq_as_asr_only_provider():
    assert PROVIDERS["Groq"] is GroqProvider
    provider = get_provider("Groq", "k")
    assert isinstance(provider, GroqProvider)
    assert provider.supports_diarization is False
    assert provider.supports_mixed is True
