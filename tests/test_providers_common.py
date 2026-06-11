"""Direct units for providers._common — shared transport machinery.

HTTP is patched at the ONE canonical target: ``providers._common.requests``.
The per-provider test files keep their behavioral assertions and act as
integration coverage on top of these units.
"""
from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers._common import (
    cancel_remote,
    check_cancel,
    file_stream,
    guess_content_type,
    require_key,
    validate_via_get,
)
from providers.base import ProviderError

# ── check_cancel ──────────────────────────────────────────────────────


def test_check_cancel_none_event_is_noop():
    check_cancel(None)  # must not raise


def test_check_cancel_unset_event_is_noop():
    check_cancel(threading.Event())


def test_check_cancel_set_event_raises_transcription_cancelled():
    from transcriber import TranscriptionCancelled

    ev = threading.Event()
    ev.set()
    with pytest.raises(TranscriptionCancelled):
        check_cancel(ev)


# ── guess_content_type ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ext", "mime"),
    [
        (".mp3", "audio/mpeg"),
        (".wav", "audio/wav"),
        (".m4a", "audio/mp4"),
        (".flac", "audio/flac"),
        (".ogg", "audio/ogg"),
        (".webm", "audio/webm"),
        (".xyz", "application/octet-stream"),
    ],
)
def test_guess_content_type(ext, mime):
    assert guess_content_type(f"C:/audio/file{ext}") == mime


def test_guess_content_type_is_case_insensitive():
    assert guess_content_type("C:/audio/FILE.MP3") == "audio/mpeg"


# ── require_key ───────────────────────────────────────────────────────


def test_require_key_strips_and_returns():
    assert require_key("  abc  ", "AssemblyAI") == "abc"


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_require_key_empty_raises_with_provider_name(bad):
    with pytest.raises(ProviderError, match="API-ключ Gladia не задан"):
        require_key(bad, "Gladia")


# ── cancel_remote ─────────────────────────────────────────────────────


def test_cancel_remote_network_error_logged_not_raised(caplog):
    with patch(
        "providers._common.requests.delete",
        side_effect=requests.ConnectionError("boom"),
    ), caplog.at_level(logging.WARNING, logger="providers._common"):
        cancel_remote("https://api.example/jobs/42", {"h": "1"}, provider="X")
    assert any("cancel-DELETE failed" in r.message for r in caplog.records)
    assert any("jobs/42" in r.message for r in caplog.records)


def test_cancel_remote_success_no_log(caplog):
    with patch(
        "providers._common.requests.delete",
        return_value=MagicMock(ok=True, status_code=200),
    ):
        with caplog.at_level("WARNING", logger="providers._common"):
            cancel_remote("https://api.example/jobs/42", {}, provider="X")
    assert caplog.records == []


def test_cancel_remote_unexpected_exception_propagates():
    """The except is deliberately narrow (RequestException only) — a
    non-requests failure must propagate, not be silently swallowed."""
    with patch(
        "providers._common.requests.delete",
        side_effect=ValueError("not a transport error"),
    ):
        with pytest.raises(ValueError):
            cancel_remote("https://api.example/jobs/42", {}, provider="X")


# ── validate_via_get ──────────────────────────────────────────────────


def test_validate_via_get_2xx_returns_empty_dict():
    r = MagicMock(status_code=200, text="{}")
    with patch("providers._common.requests.get", return_value=r) as g:
        out = validate_via_get(
            "https://api.example/check", headers={"a": "b"}, provider="X",
            params={"limit": 1},
        )
    assert out == {}
    assert g.call_args.kwargs.get("timeout") == 15
    assert g.call_args.kwargs.get("params") == {"limit": 1}


@pytest.mark.parametrize("code", [401, 403])
def test_validate_via_get_rejected_key_is_russian(code):
    r = MagicMock(status_code=code, text="unauthorized")
    with patch("providers._common.requests.get", return_value=r):
        with pytest.raises(ProviderError, match="X отклонил ключ"):
            validate_via_get("u", headers={}, provider="X")


def test_validate_via_get_http_error_truncates_to_300():
    r = MagicMock(status_code=500, text="y" * 1000)
    with patch("providers._common.requests.get", return_value=r):
        with pytest.raises(
            ProviderError, match="проверка ключа не удалась"
        ) as ei:
            validate_via_get("u", headers={}, provider="X")
    assert "y" * 300 in str(ei.value)
    assert "y" * 301 not in str(ei.value)


def test_validate_via_get_network_failure():
    with patch(
        "providers._common.requests.get",
        side_effect=requests.RequestException("boom"),
    ):
        with pytest.raises(
            ProviderError, match="Сеть не отвечает при проверке ключа"
        ):
            validate_via_get("u", headers={}, provider="X")


# ── file_stream ───────────────────────────────────────────────────────


def test_file_stream_yields_all_bytes_and_band_progress(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"0123456789")  # 10 bytes
    calls: list[float] = []
    chunks = list(
        file_stream(
            str(f), cancel_event=None, on_progress=calls.append, chunk_size=4,
        )
    )
    assert b"".join(chunks) == b"0123456789"
    # 4/10, 8/10, 10/10 of the default 70 % band
    assert calls == pytest.approx([28.0, 56.0, 70.0])


def test_file_stream_no_progress_callback_ok(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"xy")
    assert b"".join(
        file_stream(str(f), cancel_event=None, on_progress=None)
    ) == b"xy"


def test_file_stream_cancel_mid_stream(tmp_path):
    from transcriber import TranscriptionCancelled

    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 10)
    ev = threading.Event()
    gen = file_stream(str(f), cancel_event=ev, on_progress=None, chunk_size=4)
    assert next(gen) == b"xxxx"
    ev.set()
    with pytest.raises(TranscriptionCancelled):
        next(gen)
