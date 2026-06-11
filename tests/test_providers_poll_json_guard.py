"""WS-3: provider poll loops must guard r.json().

A 200-OK poll response with a non-JSON body (proxy interstitial, gateway
HTML, captive portal) makes ``r.json()`` raise ``ValueError`` — which is NOT
a ``ProviderError``, so before the fix it escaped the provider contract and
surfaced in the UI as a raw traceback instead of the friendly Russian message
the ``except ProviderError`` arm in transcriber expects.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from providers.base import ProviderError


def _non_json_ok_response() -> MagicMock:
    """A 200-OK response whose body is not JSON (r.json() raises ValueError)."""
    r = MagicMock()
    r.ok = True
    r.status_code = 200
    r.text = "<html>502 Bad Gateway</html>"
    r.json.side_effect = ValueError("No JSON could be decoded")
    return r


def test_assemblyai_poll_non_json_raises_providererror():
    from providers.assemblyai import AssemblyAIProvider
    p = AssemblyAIProvider("test-key")
    with patch("providers._common.requests.get", return_value=_non_json_ok_response()):
        with pytest.raises(ProviderError):
            p._poll("transcript-id", None, None)


def test_gladia_poll_non_json_raises_providererror():
    from providers.gladia import GladiaProvider
    p = GladiaProvider("test-key")
    with patch("providers._common.requests.get", return_value=_non_json_ok_response()):
        with pytest.raises(ProviderError):
            p._poll("https://api.gladia.io/v2/transcription/x/result", None, None)


def test_speechmatics_poll_non_json_raises_providererror():
    from providers.speechmatics import SpeechmaticsProvider
    p = SpeechmaticsProvider("test-key")
    with patch("providers._common.requests.get", return_value=_non_json_ok_response()):
        with pytest.raises(ProviderError):
            p._wait_for_job("job-id", None, None)
