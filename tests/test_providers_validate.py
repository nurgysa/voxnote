"""validate_key() — cheap server-side auth check on all 4 cloud providers.

Contract: returns a dict on success; raises ProviderError with a Russian,
user-actionable message on a rejected key (401/403) or network failure.
The Settings «Проверить» button (api_key_row) calls this in a worker thread.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers import ProviderError
from providers.assemblyai import AssemblyAIProvider
from providers.base import TranscriptionProvider
from providers.deepgram import DeepgramProvider
from providers.gladia import GladiaProvider
from providers.speechmatics import SpeechmaticsProvider


def _resp(status: int, text: str = "") -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = text
    return m


# ── base default ──────────────────────────────────────────────────────


def test_base_default_refuses_with_provider_error():
    class _Stub(TranscriptionProvider):
        def __init__(self):
            pass

        def transcribe(self, *a, **k):  # pragma: no cover - never called
            raise NotImplementedError

    with pytest.raises(ProviderError, match="не поддерживает"):
        _Stub().validate_key()


# ── per-provider: ok / rejected / network ─────────────────────────────


@pytest.mark.parametrize(
    ("cls", "module", "url_part"),
    [
        (AssemblyAIProvider, "providers.assemblyai", "/transcript"),
        (DeepgramProvider, "providers.deepgram", "auth/token"),
        (GladiaProvider, "providers.gladia", "/pre-recorded"),
        (SpeechmaticsProvider, "providers.speechmatics", "/jobs"),
    ],
)
def test_validate_ok_hits_cheap_endpoint(cls, module, url_part):
    p = cls("test-key")
    with patch(f"{module}.requests.get", return_value=_resp(200, "{}")) as g:
        info = p.validate_key()
    assert isinstance(info, dict)
    called_url = g.call_args[0][0]
    assert url_part in called_url
    # the call must be authenticated and bounded
    assert g.call_args.kwargs.get("timeout")


@pytest.mark.parametrize(
    ("cls", "module"),
    [
        (AssemblyAIProvider, "providers.assemblyai"),
        (DeepgramProvider, "providers.deepgram"),
        (GladiaProvider, "providers.gladia"),
        (SpeechmaticsProvider, "providers.speechmatics"),
    ],
)
def test_validate_rejected_key_raises_russian_provider_error(cls, module):
    p = cls("bad-key")
    with patch(f"{module}.requests.get", return_value=_resp(401, "unauthorized")):
        with pytest.raises(ProviderError, match="ключ"):
            p.validate_key()


@pytest.mark.parametrize(
    ("cls", "module"),
    [
        (AssemblyAIProvider, "providers.assemblyai"),
        (DeepgramProvider, "providers.deepgram"),
        (GladiaProvider, "providers.gladia"),
        (SpeechmaticsProvider, "providers.speechmatics"),
    ],
)
def test_validate_network_failure_raises_provider_error(cls, module):
    p = cls("test-key")
    with patch(
        f"{module}.requests.get",
        side_effect=requests.RequestException("boom"),
    ):
        with pytest.raises(ProviderError, match="Сеть"):
            p.validate_key()
