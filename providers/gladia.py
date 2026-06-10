"""Gladia transcription provider.

API workflow (three calls):

    1. POST /v2/upload         multipart audio → {audio_url}
    2. POST /v2/pre-recorded   {audio_url, diarization, ...} → {id, result_url}
    3. GET  <result_url>       every few seconds until status="done".

Gladia runs Whisper + pyannote in their cloud, so the result shape is
structurally identical to this app's local pipeline. ``utterances``
already arrive in turn form ({start, end, text, speaker}), making the
adapter near-trivial.

Pricing (Mar 2026):
  ~$0.61/h with diarization (Pay-as-you-go tier).

Languages: 95+ via Whisper-Large; including ``ru`` and ``kk``.
"""

from __future__ import annotations

import os
import time

import requests

from .base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)

_API_BASE = "https://api.gladia.io/v2"
# Polling cadence for transcript completion. 3 s matches the AssemblyAI
# provider — fast enough to keep total wall-time tight, slow enough that
# we don't hammer the API.
_POLL_INTERVAL_S = 3.0
# Hard cap on total processing wait. Generous safety net.
_MAX_WAIT_S = 90 * 60


class GladiaProvider(TranscriptionProvider):
    """Cloud transcription via api.gladia.io (Whisper + pyannote)."""

    display_name = "Gladia"
    supports_diarization = True
    supports_mixed = True   # Gladia supports KZ+RU+EN via code_switching flag

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise ProviderError(
                "API-ключ Gladia не задан. Открой Настройки → Облако и "
                "вставь ключ."
            )
        self._api_key = api_key.strip()
        self._headers = {"x-gladia-key": self._api_key}

    def validate_key(self) -> dict:
        """Cheap auth check: GET /pre-recorded?limit=1 — 2xx means the key is live."""
        try:
            r = requests.get(
                f"{_API_BASE}/pre-recorded", params={"limit": 1},
                headers=self._headers, timeout=15,
            )
        except requests.RequestException as e:
            raise ProviderError(f"Сеть не отвечает при проверке ключа: {e}") from e
        if r.status_code in (401, 403):
            raise ProviderError(
                "Gladia отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if r.status_code >= 400:
            raise ProviderError(
                f"Gladia: проверка ключа не удалась ({r.status_code}): "
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
            on_status("Загрузка аудио в Gladia...")

        audio_url = self._upload(audio_path, on_progress, cancel_event)

        self._check_cancel(cancel_event)
        if on_status:
            on_status("Запуск задачи...")

        result_url = self._submit(audio_url, options)

        if on_status:
            on_status("Обработка на серверах Gladia...")

        payload = self._poll(result_url, on_status, cancel_event)

        if on_progress:
            on_progress(100.0)

        segments = _to_segments(payload, want_diarization=options.diarize)
        return TranscriptionResult(
            segments=segments,
            language=_extract_language(payload),
            raw=payload,
        )

    # ------------------------- HTTP primitives -------------------------

    def _upload(self, path: str, on_progress, cancel_event) -> str:
        """Multipart upload of the audio file. Returns the audio_url.

        We pass the file handle straight to requests; multipart streaming
        in plain ``requests`` requires ``requests-toolbelt`` which the app
        doesn't depend on. Typical meeting recordings (~50-300 MB) fit
        comfortably in 16 GB RAM, so the buffered path is acceptable.
        Progress jumps from start to 50 % once upload completes — the
        polling phase below carries 50 → 100 %.
        """
        if on_progress:
            on_progress(5.0)
        with open(path, "rb") as f:
            files = {
                "audio": (
                    os.path.basename(path), f, _guess_content_type(path),
                )
            }
            try:
                r = requests.post(
                    f"{_API_BASE}/upload",
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
                "Gladia отклонил ключ (401). Проверь API-ключ в "
                "Настройках → Облако."
            )
        if not r.ok:
            raise ProviderError(
                f"Gladia upload failed ({r.status_code}): {r.text[:300]}"
            )

        if on_progress:
            on_progress(50.0)

        try:
            return r.json()["audio_url"]
        except (ValueError, KeyError) as e:
            raise ProviderError(
                f"Неожиданный ответ Gladia на upload: {r.text[:300]}"
            ) from e

    def _submit(self, audio_url: str, options: TranscriptionOptions) -> str:
        """POST /v2/pre-recorded — kick off the job. Returns the result_url."""
        body: dict = {
            "audio_url": audio_url,
            "diarization": bool(options.diarize),
        }
        if options.language == "mixed":
            # KZ+RU+EN code-switching mode. Gladia's code_switching flag
            # enables true per-segment language switching across the listed
            # languages; without it, Gladia forces a single dominant language.
            # Verified against https://docs.gladia.io/chapters/language/code-switching.md
            # (2026-05-21): field is nested in language_config, Kazakh code is "kk".
            body["language_config"] = {
                "languages": ["kk", "ru", "en"],
                "code_switching": True,
            }
        elif options.language:
            # Single forced language (kk/ru/en); code_switching stays False.
            body["language_config"] = {"languages": [options.language]}
        if options.hotwords:
            body["custom_vocabulary"] = list(options.hotwords)
        if options.diarize:
            dconf: dict = {}
            if options.num_speakers is not None:
                dconf["number_of_speakers"] = int(options.num_speakers)
            if options.min_speakers is not None:
                dconf["min_speakers"] = int(options.min_speakers)
            if options.max_speakers is not None:
                dconf["max_speakers"] = int(options.max_speakers)
            if dconf:
                body["diarization_config"] = dconf

        try:
            r = requests.post(
                f"{_API_BASE}/pre-recorded",
                headers={**self._headers, "content-type": "application/json"},
                json=body,
                timeout=30,
            )
        except requests.RequestException as e:
            raise ProviderError(
                f"Сеть не отвечает при постановке задачи: {e}"
            ) from e

        if r.status_code == 401:
            raise ProviderError("Gladia отклонил ключ (401).")
        if not r.ok:
            raise ProviderError(
                f"Gladia submit failed ({r.status_code}): {r.text[:300]}"
            )
        try:
            return r.json()["result_url"]
        except (ValueError, KeyError) as e:
            raise ProviderError(
                f"Неожиданный ответ Gladia на submit: {r.text[:300]}"
            ) from e

    def _poll(self, result_url: str, on_status, cancel_event) -> dict:
        """Block until the job finishes. Cancel-aware, capped by _MAX_WAIT_S."""
        start = time.monotonic()
        last_status = ""
        while True:
            self._check_cancel(cancel_event)
            elapsed = time.monotonic() - start
            if elapsed > _MAX_WAIT_S:
                raise ProviderError(
                    f"Gladia не вернул результат за {int(_MAX_WAIT_S/60)} "
                    f"минут. Возможно, сервис перегружен — попробуй позже."
                )

            try:
                r = requests.get(result_url, headers=self._headers, timeout=30)
            except requests.RequestException as e:
                raise ProviderError(
                    f"Сеть не отвечает при опросе: {e}"
                ) from e
            if not r.ok:
                raise ProviderError(
                    f"Gladia poll failed ({r.status_code}): {r.text[:300]}"
                )

            try:
                payload = r.json()
            except ValueError as e:
                raise ProviderError(
                    f"Gladia вернул не-JSON ответ при опросе "
                    f"({r.status_code}): {r.text[:300]}"
                ) from e
            status = payload.get("status")

            if status != last_status and on_status is not None:
                pretty = {
                    "queued": "В очереди Gladia...",
                    "processing": "Обработка на серверах Gladia...",
                }.get(status, f"Gladia: {status}")
                on_status(pretty)
                last_status = status

            if status == "done":
                return payload
            if status == "error":
                err = (payload.get("error_code")
                       or payload.get("error") or "<no detail>")
                raise ProviderError(f"Gladia вернул ошибку: {err}")

            # 0.25 s slice for cancel responsiveness.
            slept = 0.0
            while slept < _POLL_INTERVAL_S:
                self._check_cancel(cancel_event)
                time.sleep(0.25)
                slept += 0.25

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


def _extract_language(payload: dict) -> str | None:
    res = payload.get("result") or {}
    tr = res.get("transcription") or {}
    langs = tr.get("languages") or []
    if isinstance(langs, list) and langs:
        return str(langs[0])
    return None


def _to_segments(payload: dict, want_diarization: bool) -> list[dict]:
    """Convert Gladia response → internal segment shape.

    Gladia returns:
      result.transcription.utterances[]:
        {start, end, text, speaker?, words?}

    Each utterance is already a turn — one output segment per utterance.
    Speaker labels are emitted as ``SPEAKER_<int>`` so the «Спикер N»
    rewrite path in ``transcript_format`` picks them up identically to
    pyannote labels from the local pipeline.
    """
    res = payload.get("result") or {}
    tr = res.get("transcription") or {}
    utts = tr.get("utterances") or []
    if not utts:
        text = (tr.get("full_transcript") or "").strip()
        return [{"start": 0.0, "end": 0.0, "text": text}] if text else []

    out: list[dict] = []
    for u in utts:
        seg: dict = {
            "start": float(u.get("start", 0.0)),
            "end": float(u.get("end", 0.0)),
            "text": (u.get("text") or "").strip(),
        }
        if want_diarization and u.get("speaker") is not None:
            seg["speaker"] = f"SPEAKER_{u['speaker']}"
        out.append(seg)
    return out
