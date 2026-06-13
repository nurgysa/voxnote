"""Tests for the Hermes webhook client.

All nine spec §11.2 client behaviors are covered. Network is patched at
``integrations.hermes.client.requests.post`` — the canonical house pattern
(patch where it is USED). No new test dependencies beyond stdlib unittest.mock.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from integrations.hermes.client import (
    HermesWebhookConfig,
    HermesWebhookResult,
    emit_audio_transcribed_event,
    post_event,
    serialize_payload,
    sign_body,
)

# ── helpers ──────────────────────────────────────────────────────────

_ENABLED_CONFIG = HermesWebhookConfig(
    enabled=True,
    url="http://localhost:8644/webhooks/audio-transcribed",
    secret="s3cr3t",
    timeout_seconds=5.0,
    routing_hint="obsidian_inbox",
)

_SIMPLE_PAYLOAD = {"event_type": "audio.transcribed", "version": "1.0"}


# ── 1. Disabled config makes no request ──────────────────────────────

def test_disabled_config_no_request():
    config = HermesWebhookConfig(enabled=False)
    with patch("integrations.hermes.client.requests.post") as mock_post:
        result = post_event(_SIMPLE_PAYLOAD, config)
    mock_post.assert_not_called()
    assert result.enabled is False
    assert result.sent is False
    assert result.error is None


# ── 2. Enabled + empty URL → error, no request ───────────────────────

def test_empty_url_returns_error_no_request():
    config = HermesWebhookConfig(enabled=True, url="", secret="x")
    with patch("integrations.hermes.client.requests.post") as mock_post:
        result = post_event(_SIMPLE_PAYLOAD, config)
    mock_post.assert_not_called()
    assert result.enabled is True
    assert result.sent is False
    assert result.error is not None


# ── 3. Enabled + empty secret → error, no request ────────────────────

def test_empty_secret_returns_error_no_request():
    config = HermesWebhookConfig(
        enabled=True,
        url="http://localhost:8644/webhooks/audio-transcribed",
        secret="",
    )
    with patch("integrations.hermes.client.requests.post") as mock_post:
        result = post_event(_SIMPLE_PAYLOAD, config)
    mock_post.assert_not_called()
    assert result.enabled is True
    assert result.sent is False
    assert result.error is not None


# ── 4. serialize_payload is deterministic ────────────────────────────

def test_serialize_payload_deterministic():
    payload = {"z": 1, "a": 2, "m": [3, 4]}
    b1 = serialize_payload(payload)
    b2 = serialize_payload(payload)
    assert b1 == b2
    assert isinstance(b1, bytes)


def test_serialize_payload_sort_keys():
    payload = {"z": 1, "a": 2}
    body = serialize_payload(payload)
    decoded = body.decode("utf-8")
    assert decoded.index('"a"') < decoded.index('"z"')


def test_serialize_payload_compact_separators():
    payload = {"k": "v"}
    body = serialize_payload(payload)
    # Compact: no spaces around separators
    assert b" " not in body


def test_serialize_payload_unicode_not_ascii_escaped():
    payload = {"t": "Привет"}
    body = serialize_payload(payload)
    assert "Привет".encode() in body


# ── 5. sign_body matches known HMAC-SHA256 ───────────────────────────

def test_sign_body_known_hmac():
    body = b'{"event_type":"audio.transcribed"}'
    secret = "test-secret"
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sign_body(secret, body) == expected


def test_sign_body_returns_hex_string():
    sig = sign_body("key", b"data")
    assert isinstance(sig, str)
    int(sig, 16)  # must be valid hex; raises ValueError if not


# ── 6. Successful POST sends correct URL, body, headers, timeout ─────

def test_successful_post_sends_correct_request():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp) as mock_post:
        result = post_event(_SIMPLE_PAYLOAD, _ENABLED_CONFIG)

    assert result.sent is True
    assert result.status_code == 200
    assert result.error is None

    mock_post.assert_called_once()
    call_args = mock_post.call_args

    # Positional arg[0] or keyword 'url' must be the configured URL
    called_url = call_args.args[0] if call_args.args else call_args.kwargs["url"]
    assert called_url == _ENABLED_CONFIG.url

    # data= must be the exact serialized bytes (NOT json=)
    expected_body = serialize_payload(_SIMPLE_PAYLOAD)
    called_data = call_args.kwargs.get("data")
    assert called_data == expected_body, (
        "POST body must be serialize_payload() bytes passed as data=, not json="
    )

    # Headers
    headers = call_args.kwargs.get("headers", {})
    assert headers.get("Content-Type") == "application/json"
    assert "X-Webhook-Signature" in headers
    assert "X-Request-ID" in headers

    # Timeout
    assert call_args.kwargs.get("timeout") == _ENABLED_CONFIG.timeout_seconds


def test_x_webhook_signature_matches_body():
    """The signature in the header must match HMAC over the exact body bytes."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp) as mock_post:
        post_event(_SIMPLE_PAYLOAD, _ENABLED_CONFIG)

    call_kwargs = mock_post.call_args.kwargs
    body_bytes = call_kwargs["data"]
    header_sig = call_kwargs["headers"]["X-Webhook-Signature"]
    expected_sig = sign_body(_ENABLED_CONFIG.secret, body_bytes)
    assert header_sig == expected_sig


def test_x_request_id_format():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp) as mock_post:
        post_event(_SIMPLE_PAYLOAD, _ENABLED_CONFIG)

    req_id = mock_post.call_args.kwargs["headers"]["X-Request-ID"]
    assert req_id.startswith("voxnote:")


# ── 7. Non-2xx returns sent=False with status code ───────────────────

def test_non_2xx_returns_sent_false():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.ok = False

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp):
        result = post_event(_SIMPLE_PAYLOAD, _ENABLED_CONFIG)

    assert result.sent is False
    assert result.status_code == 503
    assert result.error is not None


# ── 8. requests.RequestException is caught, returned as error ────────

def test_request_exception_caught():
    with patch("integrations.hermes.client.requests.post",
               side_effect=requests.RequestException("timeout")):
        result = post_event(_SIMPLE_PAYLOAD, _ENABLED_CONFIG)

    assert result.sent is False
    assert result.error is not None
    # Must not raise


# ── 9. Secret never appears in result error ───────────────────────────

def test_secret_not_in_error_for_non_2xx():
    config = HermesWebhookConfig(
        enabled=True,
        url="http://localhost:8644/webhooks/audio-transcribed",
        secret="super-secret-value",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.ok = False

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp):
        result = post_event(_SIMPLE_PAYLOAD, config)

    assert "super-secret-value" not in (result.error or "")


def test_secret_not_in_error_for_request_exception():
    config = HermesWebhookConfig(
        enabled=True,
        url="http://localhost:8644/webhooks/audio-transcribed",
        secret="super-secret-value",
        # RequestException message will NOT contain secret — but verify anyway
    )
    with patch("integrations.hermes.client.requests.post",
               side_effect=requests.RequestException("connection refused")):
        result = post_event(_SIMPLE_PAYLOAD, config)

    assert "super-secret-value" not in (result.error or "")


# ── emit_audio_transcribed_event convenience function ─────────────────

def test_emit_convenience_calls_post():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True

    with patch("integrations.hermes.client.requests.post",
               return_value=mock_resp) as mock_post:
        result = emit_audio_transcribed_event(
            config=_ENABLED_CONFIG,
            transcript_text="Тест",
            audio_path="C:/tmp/test.m4a",
            provider="Deepgram",
            language="ru",
        )

    assert result.sent is True
    mock_post.assert_called_once()


def test_emit_disabled_config_no_request():
    config = HermesWebhookConfig(enabled=False)
    with patch("integrations.hermes.client.requests.post") as mock_post:
        result = emit_audio_transcribed_event(
            config=config,
            transcript_text="x",
        )
    mock_post.assert_not_called()
    assert result.sent is False


# ── get_hermes_webhook_config (spec §9.3) ────────────────────────────

from integrations.hermes.client import get_hermes_webhook_config  # noqa: E402


def test_config_defaults_when_empty_dict():
    """Missing config → disabled, defaults for all other fields."""
    cfg = get_hermes_webhook_config({})
    assert cfg.enabled is False
    assert cfg.url == "http://localhost:8644/webhooks/audio-transcribed"
    assert cfg.secret == ""
    assert cfg.timeout_seconds == 10.0
    assert cfg.routing_hint == "obsidian_inbox"


def test_config_defaults_when_none():
    """config=None treated same as {}."""
    cfg = get_hermes_webhook_config(None)
    assert cfg.enabled is False


def test_config_file_values_honored():
    """Values from the config dict are used when env is absent."""
    cfg = get_hermes_webhook_config({
        "hermes_webhook_enabled": True,
        "hermes_webhook_url": "http://example.com/webhooks/audio-transcribed",
        "hermes_webhook_secret": "mysecret",
        "hermes_webhook_timeout_seconds": 30,
        "hermes_webhook_routing_hint": "my_inbox",
    })
    assert cfg.enabled is True
    assert cfg.url == "http://example.com/webhooks/audio-transcribed"
    assert cfg.secret == "mysecret"
    assert cfg.timeout_seconds == 30.0
    assert cfg.routing_hint == "my_inbox"


def test_env_overrides_config(monkeypatch):
    """Env vars take precedence over config-file values."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_URL", "http://env-host/wh")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_SECRET", "envsecret")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ROUTING_HINT", "telegram_inbox")

    cfg = get_hermes_webhook_config({
        "hermes_webhook_enabled": False,
        "hermes_webhook_url": "http://config-host/wh",
        "hermes_webhook_secret": "configsecret",
        "hermes_webhook_timeout_seconds": 99,
        "hermes_webhook_routing_hint": "config_inbox",
    })

    assert cfg.enabled is True
    assert cfg.url == "http://env-host/wh"
    assert cfg.secret == "envsecret"
    assert cfg.timeout_seconds == 7.0
    assert cfg.routing_hint == "telegram_inbox"


@pytest.mark.parametrize("value,expected", [
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("1", True),
    ("yes", True),
    ("YES", True),
    ("on", True),
    ("ON", True),
    ("false", False),
    ("False", False),
    ("0", False),
    ("no", False),
    ("", False),
    ("random", False),
])
def test_bool_parsing_via_env(monkeypatch, value, expected):
    """All recognised bool strings parsed correctly via env override."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", value)
    cfg = get_hermes_webhook_config({})
    assert cfg.enabled is expected, f"bool({value!r}) should be {expected}"


def test_empty_env_string_falls_through_to_config(monkeypatch):
    """Empty env string = unset (cli.config.resolve semantics): it must not
    clobber a configured value — uniformly across all five fields."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_ENABLED", "")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS", "")
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_SECRET", "")
    cfg = get_hermes_webhook_config({
        "hermes_webhook_enabled": True,
        "hermes_webhook_timeout_seconds": 25,
        "hermes_webhook_secret": "configsecret",
    })
    assert cfg.enabled is True
    assert cfg.timeout_seconds == 25.0
    assert cfg.secret == "configsecret"


def test_real_bool_true_passthrough():
    """A real Python True in config dict is accepted as-is."""
    cfg = get_hermes_webhook_config({"hermes_webhook_enabled": True})
    assert cfg.enabled is True


def test_real_bool_false_passthrough():
    """A real Python False in config dict is accepted as-is."""
    cfg = get_hermes_webhook_config({"hermes_webhook_enabled": False})
    assert cfg.enabled is False


def test_bad_timeout_falls_back():
    """Non-numeric timeout value falls back to 10.0."""
    cfg = get_hermes_webhook_config({"hermes_webhook_timeout_seconds": "bogus"})
    assert cfg.timeout_seconds == 10.0


def test_zero_timeout_falls_back():
    """Zero timeout falls back to 10.0 (non-positive guard)."""
    cfg = get_hermes_webhook_config({"hermes_webhook_timeout_seconds": 0})
    assert cfg.timeout_seconds == 10.0


def test_negative_timeout_falls_back():
    """Negative timeout falls back to 10.0."""
    cfg = get_hermes_webhook_config({"hermes_webhook_timeout_seconds": -5})
    assert cfg.timeout_seconds == 10.0


def test_bad_timeout_env_falls_back(monkeypatch):
    """Non-numeric env timeout also falls back."""
    monkeypatch.setenv("VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS", "notanumber")
    cfg = get_hermes_webhook_config({})
    assert cfg.timeout_seconds == 10.0


def test_missing_config_disabled():
    """Completely empty config → feature disabled (safe default)."""
    cfg = get_hermes_webhook_config({})
    assert cfg.enabled is False
