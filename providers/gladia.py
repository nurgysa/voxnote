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

from ._common import (
    PollSpec,
    check_cancel,
    extract_json_key,
    guess_content_type,
    poll,
    request,
    require_key,
    validate_via_get,
)
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


class GladiaProvider(TranscriptionProvider):
    """Cloud transcription via api.gladia.io (Whisper + pyannote)."""

    display_name = "Gladia"
    supports_diarization = True
    supports_mixed = True   # Gladia supports KZ+RU+EN via code_switching flag

    def __init__(self, api_key: str):
        self._api_key = require_key(api_key, "Gladia")
        self._headers = {"x-gladia-key": self._api_key}

    def validate_key(self) -> dict:
        """Cheap auth check: GET /pre-recorded?limit=1 — 2xx means the key is live."""
        return validate_via_get(
            f"{_API_BASE}/pre-recorded", headers=self._headers,
            provider=self.display_name, params={"limit": 1},
        )

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

        check_cancel(cancel_event)
        if on_status:
            on_status("Загрузка аудио в Gladia...")

        audio_url = self._upload(audio_path, on_progress, cancel_event)

        check_cancel(cancel_event)
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
                    os.path.basename(path), f, guess_content_type(path),
                )
            }
            r = request(
                "post",
                f"{_API_BASE}/upload",
                provider=self.display_name,
                action_ru="загрузке аудио",
                action_en="upload",
                timeout=60 * 30,
                headers=self._headers,
                files=files,
            )
        if on_progress:
            on_progress(50.0)
        return extract_json_key(
            r, "audio_url", provider=self.display_name, context="upload",
        )

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

        r = request(
            "post",
            f"{_API_BASE}/pre-recorded",
            provider=self.display_name,
            action_ru="постановке задачи",
            action_en="submit",
            timeout=30,
            headers={**self._headers, "content-type": "application/json"},
            json=body,
        )
        return extract_json_key(
            r, "result_url", provider=self.display_name, context="submit",
        )

    def _poll(self, result_url: str, on_status, cancel_event) -> dict:
        """Block until the job finishes — shared loop, Gladia knobs."""
        spec = PollSpec(
            url=result_url,
            headers=self._headers,
            provider=self.display_name,
            interval_s=_POLL_INTERVAL_S,
            extract_status=lambda p: p.get("status"),
            done_statuses=frozenset({"done"}),
            error_statuses=frozenset({"error"}),
            extract_error=lambda p: (
                p.get("error_code") or p.get("error") or "<no detail>"
            ),
            pretty={
                "queued": "В очереди Gladia...",
                "processing": "Обработка на серверах Gladia...",
            },
        )
        return poll(spec, on_status=on_status, cancel_event=cancel_event)


# ---------------------------- helpers ---------------------------------


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
