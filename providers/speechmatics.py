"""Speechmatics transcription provider.

API workflow (three calls):

    1. POST /v2/jobs/                multipart {data_file, config}
       → {id}
    2. GET  /v2/jobs/{id}            poll job.status until done | rejected
    3. GET  /v2/jobs/{id}/transcript?format=json-v2
       → word-level results with speaker labels.

Pricing (Mar 2026):
  Standard model: ~$1.04/h with speaker diarization. ~2 GB upload cap.

Languages: 50+, including ``ru`` and ``kk``.
"""

from __future__ import annotations

import json
import os
import time

import requests

from .base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)

_API_BASE = "https://asr.api.speechmatics.com/v2"
_POLL_INTERVAL_S = 5.0    # Speechmatics is slower than Deepgram/AssemblyAI.
_MAX_WAIT_S = 90 * 60


class SpeechmaticsProvider(TranscriptionProvider):
    """Cloud transcription via asr.api.speechmatics.com."""

    display_name = "Speechmatics"
    supports_diarization = True
    supports_mixed = True  # KZ in multilingual model + language_identification_config

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ProviderError(
                "API-ключ Speechmatics не задан. Открой Настройки → "
                "Облако и вставь ключ."
            )
        self._api_key = api_key.strip()
        self._headers = {"Authorization": f"Bearer {self._api_key}"}

    def validate_key(self) -> dict:
        """Cheap auth check: GET /jobs/?limit=1 — 2xx means the key is live."""
        try:
            r = requests.get(
                f"{_API_BASE}/jobs/", params={"limit": 1},
                headers=self._headers, timeout=15,
            )
        except requests.RequestException as e:
            raise ProviderError(f"Сеть не отвечает при проверке ключа: {e}") from e
        if r.status_code in (401, 403):
            raise ProviderError(
                "Speechmatics отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if r.status_code >= 400:
            raise ProviderError(
                f"Speechmatics: проверка ключа не удалась ({r.status_code}): "
                f"{r.text[:200]}"
            )
        return {}

    # --------------------------- public API ----------------------------

    def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions,
        on_status=None,
        on_progress=None,
        cancel_event=None,
    ) -> TranscriptionResult:
        if not os.path.isfile(audio_path):
            raise ProviderError(f"Файл не найден: {audio_path}")

        self._check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в Speechmatics...")
        if on_progress:
            on_progress(5.0)

        job_id = self._submit_job(audio_path, options)

        if on_progress:
            on_progress(50.0)
        if on_status:
            on_status("Обработка на серверах Speechmatics...")

        try:
            self._wait_for_job(job_id, on_status, cancel_event)
            payload = self._fetch_transcript(job_id)
        except Exception:
            self._cancel_remote(job_id)
            raise

        if on_progress:
            on_progress(100.0)

        segments = _to_segments(payload, want_diarization=options.diarize)
        return TranscriptionResult(
            segments=segments,
            language=_extract_language(payload),
            raw=payload,
        )

    # ------------------------- HTTP primitives -------------------------

    def _submit_job(self, path: str, options: TranscriptionOptions) -> str:
        """POST /v2/jobs/ — uploads the audio and submits a config blob.

        The config travels as a multipart form field named ``config``,
        JSON-encoded — Speechmatics' standard pattern.
        """
        config = _build_config(options)
        with open(path, "rb") as f:
            files = {
                "data_file": (
                    os.path.basename(path), f, _guess_content_type(path),
                ),
                "config": (None, json.dumps(config), "application/json"),
            }
            try:
                r = requests.post(
                    f"{_API_BASE}/jobs/",
                    headers=self._headers,
                    files=files,
                    timeout=60 * 30,
                )
            except requests.RequestException as e:
                raise ProviderError(
                    f"Сеть не отвечает при загрузке аудио: {e}"
                ) from e

        if r.status_code == 401:
            raise ProviderError(
                "Speechmatics отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if not r.ok:
            raise ProviderError(
                f"Speechmatics submit failed ({r.status_code}): "
                f"{r.text[:300]}"
            )

        try:
            return r.json()["id"]
        except (ValueError, KeyError) as e:
            raise ProviderError(
                f"Неожиданный ответ Speechmatics на submit: {r.text[:300]}"
            ) from e

    def _wait_for_job(self, job_id: str, on_status, cancel_event) -> None:
        """Poll /v2/jobs/{id} until the job finishes or the deadline trips."""
        start = time.monotonic()
        last_status = ""
        while True:
            self._check_cancel(cancel_event)
            elapsed = time.monotonic() - start
            if elapsed > _MAX_WAIT_S:
                raise ProviderError(
                    f"Speechmatics не вернул результат за "
                    f"{int(_MAX_WAIT_S/60)} минут. Возможно, сервис "
                    f"перегружен — попробуй позже."
                )

            try:
                r = requests.get(
                    f"{_API_BASE}/jobs/{job_id}",
                    headers=self._headers,
                    timeout=30,
                )
            except requests.RequestException as e:
                raise ProviderError(
                    f"Сеть не отвечает при опросе: {e}"
                ) from e
            if not r.ok:
                raise ProviderError(
                    f"Speechmatics poll failed ({r.status_code}): "
                    f"{r.text[:300]}"
                )

            try:
                payload = r.json()
            except ValueError as e:
                raise ProviderError(
                    f"Speechmatics вернул не-JSON ответ при опросе "
                    f"({r.status_code}): {r.text[:300]}"
                ) from e
            status = (payload.get("job") or {}).get("status") \
                or payload.get("status")

            if status != last_status and on_status is not None:
                pretty = {
                    "running": "Обработка на серверах Speechmatics...",
                    "queued": "В очереди Speechmatics...",
                }.get(status, f"Speechmatics: {status}")
                on_status(pretty)
                last_status = status

            if status == "done":
                return
            if status in ("rejected", "deleted", "expired"):
                err = (payload.get("job") or {}).get("errors") \
                    or "<no detail>"
                raise ProviderError(f"Speechmatics вернул ошибку: {err}")

            slept = 0.0
            while slept < _POLL_INTERVAL_S:
                self._check_cancel(cancel_event)
                time.sleep(0.25)
                slept += 0.25

    def _fetch_transcript(self, job_id: str) -> dict:
        """GET /v2/jobs/{id}/transcript?format=json-v2 — word-level result."""
        try:
            r = requests.get(
                f"{_API_BASE}/jobs/{job_id}/transcript",
                params={"format": "json-v2"},
                headers=self._headers,
                timeout=60,
            )
        except requests.RequestException as e:
            raise ProviderError(
                f"Сеть не отвечает при получении транскрипта: {e}"
            ) from e
        if not r.ok:
            raise ProviderError(
                f"Speechmatics transcript fetch failed "
                f"({r.status_code}): {r.text[:300]}"
            )
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError(
                f"Неожиданный ответ Speechmatics на transcript: "
                f"{r.text[:300]}"
            ) from e

    def _cancel_remote(self, job_id: str) -> None:
        """Best-effort DELETE on cancel — avoids being billed for a run we
        already gave up on. Errors are swallowed."""
        try:
            requests.delete(
                f"{_API_BASE}/jobs/{job_id}",
                headers=self._headers,
                timeout=10,
            )
        except Exception:
            pass

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            from transcriber import TranscriptionCancelled
            raise TranscriptionCancelled()


# ---------------------------- helpers ---------------------------------


def _guess_content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".m4a":  "audio/mp4",
        ".flac": "audio/flac",
        ".ogg":  "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")


def _build_config(options: TranscriptionOptions) -> dict:
    """Assemble the Speechmatics job config blob.

    Note: Speechmatics has no exact-count speaker hint. ``num_speakers``
    / ``min_speakers`` / ``max_speakers`` are intentionally ignored —
    the provider picks the count automatically.

    For ``language == "mixed"`` (KZ+RU+EN code-switching):
      - ``transcription_config.language`` is set to ``"auto"`` to enable
        Speechmatics' built-in language identification.
      - A top-level ``language_identification_config`` (sibling of
        ``transcription_config``, NOT nested inside it) is added with
        ``expected_languages: ["kk", "ru", "en"]`` to narrow the
        candidate set.
    Verified against:
      https://docs.speechmatics.com/speech-to-text/batch/language-identification
      on 2026-05-21.
    """
    transcription_config: dict = {}
    if options.language == "mixed":
        # KZ+RU+EN multilingual mode: opt into language ID via "auto",
        # then restrict candidates with language_identification_config.
        transcription_config["language"] = "auto"
    else:
        transcription_config["language"] = options.language or "auto"

    if options.diarize:
        transcription_config["diarization"] = "speaker"
    if options.hotwords:
        transcription_config["additional_vocab"] = [
            {"content": w} for w in options.hotwords if w
        ]

    config: dict = {
        "type": "transcription",
        "transcription_config": transcription_config,
    }
    if options.language == "mixed":
        # Top-level sibling of transcription_config — per Speechmatics docs.
        config["language_identification_config"] = {
            "expected_languages": ["kk", "ru", "en"],
        }
    return config


def _extract_language(payload: dict) -> str | None:
    meta = payload.get("metadata") or {}
    cfg = (meta.get("transcription_config") or {})
    lang = cfg.get("language")
    return str(lang) if lang else None


def _to_segments(payload: dict, want_diarization: bool) -> list[dict]:
    """Convert Speechmatics json-v2 response → internal segment shape.

    The response is a flat list of items typed as ``word`` or
    ``punctuation``:

        results[]: {
            type, start_time, end_time,
            alternatives: [{content, confidence, speaker?}]
        }

    Punctuation items attach to the preceding word in our output (no
    extra segment break). We start a new segment on speaker change or
    sentence-ending punctuation, mirroring the Deepgram adapter.

    Speakers come as ``"S1"``, ``"S2"``… — we re-prefix to ``SPEAKER_1``
    so the «Спикер N» rewrite path treats them identically to pyannote
    output.
    """
    items = payload.get("results") or []
    if not items:
        return []

    segments: list[dict] = []
    cur_tokens: list[str] = []
    cur_speaker: str | None = None
    seg_start: float | None = None
    seg_end: float = 0.0

    def _flush() -> None:
        nonlocal cur_tokens, cur_speaker, seg_start
        if not cur_tokens or seg_start is None:
            return
        text = "".join(cur_tokens).strip()
        if not text:
            cur_tokens = []
            seg_start = None
            return
        seg: dict = {
            "start": seg_start,
            "end": seg_end,
            "text": text,
        }
        if want_diarization and cur_speaker:
            seg["speaker"] = _normalise_speaker(cur_speaker)
        segments.append(seg)
        cur_tokens = []
        seg_start = None

    for item in items:
        alts = item.get("alternatives") or []
        if not alts:
            continue
        content = (alts[0].get("content") or "")
        if not content:
            continue
        item_type = item.get("type")
        speaker = alts[0].get("speaker")
        start = float(item.get("start_time", 0.0))
        end = float(item.get("end_time", start))

        if item_type == "punctuation":
            # Glue punctuation onto the previous token (no leading space).
            if cur_tokens:
                cur_tokens.append(content)
                seg_end = max(seg_end, end)
            if content in (".", "!", "?", "…"):
                _flush()
            continue

        # word
        if (want_diarization and cur_tokens
                and speaker and speaker != cur_speaker):
            _flush()

        if not cur_tokens:
            cur_speaker = speaker
            seg_start = start
        else:
            cur_tokens.append(" ")  # separator before next word
        seg_end = end
        cur_tokens.append(content)

    _flush()
    return segments


def _normalise_speaker(label: str) -> str:
    """Speechmatics uses ``S1``/``S2``/...; rewrite to ``SPEAKER_1``."""
    if label.startswith("S") and label[1:].isdigit():
        return f"SPEAKER_{label[1:]}"
    return f"SPEAKER_{label}"
