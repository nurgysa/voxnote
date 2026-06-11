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
    extract_json_key,
    file_stream,
    guess_content_type,
    parse_json,
    poll,
    request,
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


def test_file_stream_custom_band_scales_progress(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"0123456789")  # 10 bytes
    calls: list[float] = []
    list(file_stream(str(f), cancel_event=None, on_progress=calls.append,
                     band=40.0, chunk_size=5))
    assert calls == pytest.approx([20.0, 40.0])


def test_file_stream_empty_file_yields_nothing_no_progress(tmp_path):
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    calls: list[float] = []
    chunks = list(
        file_stream(str(f), cancel_event=None, on_progress=calls.append)
    )
    assert chunks == []
    assert calls == []  # size == 0 guard: no div-by-zero, no bogus 0%


# ── request ───────────────────────────────────────────────────────────


def test_request_dispatches_via_named_verb_for_patchability():
    r = MagicMock(ok=True, status_code=200)
    with patch("providers._common.requests.post", return_value=r) as p:
        out = request(
            "post", "https://api.example/u", provider="X",
            action_ru="загрузке аудио", action_en="upload",
            timeout=30, json={"a": 1},
        )
    assert out is r
    assert p.call_args.kwargs["timeout"] == 30
    assert p.call_args.kwargs["json"] == {"a": 1}


def test_request_network_error_uses_action_ru():
    with patch(
        "providers._common.requests.get",
        side_effect=requests.RequestException("boom"),
    ):
        with pytest.raises(
            ProviderError, match="Сеть не отвечает при опросе"
        ):
            request("get", "u", provider="X", action_ru="опросе",
                    action_en="poll", timeout=30)


@pytest.mark.parametrize("code", [401, 403])
def test_request_key_rejection_is_russian(code):
    r = MagicMock(ok=False, status_code=code, text="no")
    with patch("providers._common.requests.get", return_value=r):
        with pytest.raises(
            ProviderError, match=r"X отклонил ключ \(401\)"
        ):
            request("get", "u", provider="X", action_ru="опросе",
                    action_en="poll", timeout=30)


def test_request_non_ok_uses_action_en_and_truncates():
    r = MagicMock(ok=False, status_code=500, text="z" * 1000)
    with patch("providers._common.requests.post", return_value=r):
        with pytest.raises(
            ProviderError, match=r"X upload failed \(500\)"
        ) as ei:
            request("post", "u", provider="X", action_ru="загрузке аудио",
                    action_en="upload", timeout=30)
    assert "z" * 300 in str(ei.value)
    assert "z" * 301 not in str(ei.value)


# ── parse_json / extract_json_key ─────────────────────────────────────


def test_parse_json_ok():
    r = MagicMock()
    r.json.return_value = {"a": 1}
    assert parse_json(r, provider="X") == {"a": 1}


def test_parse_json_invalid_with_context():
    r = MagicMock(text="<html>oops</html>")
    r.json.side_effect = ValueError("no json")
    with pytest.raises(
        ProviderError, match="Неожиданный ответ X на upload: <html>oops"
    ):
        parse_json(r, provider="X", context="upload")


def test_parse_json_invalid_without_context():
    r = MagicMock(text="<html>oops</html>")
    r.json.side_effect = ValueError("no json")
    with pytest.raises(ProviderError, match="Неожиданный ответ X: <html>oops"):
        parse_json(r, provider="X")


def test_extract_json_key_ok():
    r = MagicMock()
    r.json.return_value = {"upload_url": "https://cdn/u1"}
    assert extract_json_key(
        r, "upload_url", provider="X", context="upload"
    ) == "https://cdn/u1"


def test_extract_json_key_missing_key():
    r = MagicMock(text='{"other": 1}')
    r.json.return_value = {"other": 1}
    with pytest.raises(ProviderError, match="Неожиданный ответ X на submit"):
        extract_json_key(r, "id", provider="X", context="submit")


# ── poll ──────────────────────────────────────────────────────────────


def _json_resp(payload):
    r = MagicMock(ok=True, status_code=200, text="")
    r.json.return_value = payload
    return r


def _spec(**over):
    from providers._common import PollSpec

    kw = dict(
        url="https://api.example/job/1",
        headers={"h": "1"},
        provider="X",
        interval_s=3.0,
        extract_status=lambda p: p.get("status"),
        done_statuses=frozenset({"completed"}),
        error_statuses=frozenset({"error"}),
        extract_error=lambda p: p.get("error", "<no detail>"),
        pretty={"queued": "В очереди X...", "processing": "Обработка X..."},
    )
    kw.update(over)
    return PollSpec(**kw)


def test_poll_returns_payload_on_done():
    done = {"status": "completed", "text": "hi"}
    with patch("providers._common.requests.get", return_value=_json_resp(done)):
        assert poll(_spec(), None, None) == done


def test_poll_error_status_raises_with_detail():
    bad = {"status": "error", "error": "quota exceeded"}
    with patch("providers._common.requests.get", return_value=_json_resp(bad)):
        with pytest.raises(
            ProviderError, match="X вернул ошибку: quota exceeded"
        ):
            poll(_spec(), None, None)


def test_poll_pretty_status_dedup_and_fallback():
    seq = [
        _json_resp({"status": "queued"}),
        _json_resp({"status": "queued"}),
        _json_resp({"status": "processing"}),
        _json_resp({"status": "completed"}),
    ]
    seen: list[str] = []
    with patch("providers._common.requests.get", side_effect=seq), \
         patch("providers._common.time.sleep"):
        poll(_spec(), seen.append, None)
    # one line per DISTINCT status; unmapped statuses fall back to "X: <s>"
    assert seen == ["В очереди X...", "Обработка X...", "X: completed"]


def test_poll_deadline_raises_before_get():
    with patch(
        "providers._common.time.monotonic", side_effect=[0.0, 90 * 60 + 1.0]
    ), patch("providers._common.requests.get") as g:
        with pytest.raises(
            ProviderError, match="X не вернул результат за 90 минут"
        ):
            poll(_spec(), None, None)
    g.assert_not_called()


def test_poll_non_json_raises_providererror():
    r = MagicMock(ok=True, status_code=200, text="<html>502 Bad Gateway</html>")
    r.json.side_effect = ValueError("no json")
    with patch("providers._common.requests.get", return_value=r):
        with pytest.raises(
            ProviderError, match="X вернул не-JSON ответ при опросе"
        ):
            poll(_spec(), None, None)


def test_poll_cancel_between_polls_raises():
    from transcriber import TranscriptionCancelled

    ev = threading.Event()
    with patch(
        "providers._common.requests.get",
        return_value=_json_resp({"status": "queued"}),
    ), patch(
        "providers._common.time.sleep", side_effect=lambda _s: ev.set()
    ):
        with pytest.raises(TranscriptionCancelled):
            poll(_spec(), None, ev)
