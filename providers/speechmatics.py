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

from ._common import (
    PollSpec,
    cancel_remote,
    check_cancel,
    extract_json_key,
    guess_content_type,
    parse_json,
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

_API_BASE = "https://asr.api.speechmatics.com/v2"
_POLL_INTERVAL_S = 5.0    # Speechmatics is slower than Deepgram/AssemblyAI.


class SpeechmaticsProvider(TranscriptionProvider):
    """Cloud transcription via asr.api.speechmatics.com."""

    display_name = "Speechmatics"
    supports_diarization = True
    supports_mixed = True  # KZ in multilingual model + language_identification_config
    supports_speaker_id = True  # get_speakers + speaker_diarization_config.speakers

    def __init__(self, api_key: str):
        self._api_key = require_key(api_key, "Speechmatics")
        self._headers = {"Authorization": f"Bearer {self._api_key}"}

    def validate_key(self) -> dict:
        """Cheap auth check: GET /jobs/?limit=1 — 2xx means the key is live."""
        return validate_via_get(
            f"{_API_BASE}/jobs/", headers=self._headers,
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
            # Best-effort cancel on the server side so the user isn't billed
            # for a full run after we've already given up locally.
            self._cancel_remote(job_id)
            raise

        if on_progress:
            on_progress(100.0)

        known_labels = frozenset(
            s["label"] for s in options.known_speakers if s.get("label")
        )
        segments = _to_segments(
            payload,
            want_diarization=options.diarize or options.enroll_speakers,
            known_labels=known_labels,
        )
        return TranscriptionResult(
            segments=segments,
            language=_extract_language(payload),
            raw=payload,
            speaker_identifiers=_parse_speaker_identifiers(payload),
            model=_extract_model(payload),
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
                    os.path.basename(path), f, guess_content_type(path),
                ),
                "config": (None, json.dumps(config), "application/json"),
            }
            r = request(
                "post",
                f"{_API_BASE}/jobs/",
                provider=self.display_name,
                action_ru="загрузке аудио",
                action_en="submit",
                timeout=60 * 30,
                headers=self._headers,
                files=files,
            )
        return extract_json_key(
            r, "id", provider=self.display_name, context="submit",
        )

    def _wait_for_job(self, job_id: str, on_status, cancel_event) -> None:
        """Poll /v2/jobs/{id} until done — shared loop, Speechmatics knobs.

        Returns None deliberately: the job-status payload is not the
        transcript; ``_fetch_transcript`` does the real result fetch.
        """
        spec = PollSpec(
            url=f"{_API_BASE}/jobs/{job_id}",
            headers=self._headers,
            provider=self.display_name,
            interval_s=_POLL_INTERVAL_S,
            extract_status=lambda p: (
                (p.get("job") or {}).get("status") or p.get("status")
            ),
            done_statuses=frozenset({"done"}),
            error_statuses=frozenset({"rejected", "deleted", "expired"}),
            extract_error=lambda p: (
                (p.get("job") or {}).get("errors") or "<no detail>"
            ),
            pretty={
                "running": "Обработка на серверах Speechmatics...",
                "queued": "В очереди Speechmatics...",
            },
        )
        poll(spec, on_status=on_status, cancel_event=cancel_event)

    def _fetch_transcript(self, job_id: str) -> dict:
        """GET /v2/jobs/{id}/transcript?format=json-v2 — word-level result."""
        r = request(
            "get",
            f"{_API_BASE}/jobs/{job_id}/transcript",
            provider=self.display_name,
            action_ru="получении транскрипта",
            action_en="transcript fetch",
            timeout=60,
            params={"format": "json-v2"},
            headers=self._headers,
        )
        return parse_json(r, provider=self.display_name, context="transcript")

    def _cancel_remote(self, job_id: str) -> None:
        """Best-effort server-side cancel (details in _common.cancel_remote)."""
        cancel_remote(
            f"{_API_BASE}/jobs/{job_id}",
            self._headers,
            provider=self.display_name,
        )


# ---------------------------- helpers ---------------------------------


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

    if options.enroll_speakers:
        # Speaker identification requires diarization; force it on so an
        # enroll/identify run still produces speaker-labelled segments.
        transcription_config["diarization"] = "speaker"
        sdc: dict = {"get_speakers": True}
        if options.known_speakers:
            sdc["speakers"] = [
                {"label": s["label"], "speaker_identifiers": s["identifiers"]}
                for s in options.known_speakers
            ]
        transcription_config["speaker_diarization_config"] = sdc

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


def _to_segments(
    payload: dict, want_diarization: bool, known_labels: frozenset | None = None,
) -> list[dict]:
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
    output. Labels in ``known_labels`` (real names from speaker-ID) are
    kept verbatim instead.
    """
    items = payload.get("results") or []
    if not items:
        return []

    known = known_labels or frozenset()

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
            seg["speaker"] = _normalise_speaker(cur_speaker, known)
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


def _normalise_speaker(label: str, known_labels: frozenset = frozenset()) -> str:
    """Speechmatics uses ``S1``/``S2``; rewrite to ``SPEAKER_1`` so the «Спикер N»
    path treats them like pyannote output. A label we asked to identify (in
    ``known_labels``) is a real name — keep it verbatim. ``UU`` and any other
    non-S\\d label fall through to the anonymous bucket unchanged."""
    if label in known_labels:
        return label
    if label.startswith("S") and label[1:].isdigit():
        return f"SPEAKER_{label[1:]}"
    return f"SPEAKER_{label}"


def _parse_speaker_identifiers(payload: dict) -> dict[str, list[str]] | None:
    """Top-level ``speakers`` array (present only when get_speakers was set) →
    {label: [identifier, ...]}. None when absent."""
    speakers = payload.get("speakers")
    if not speakers:
        return None
    out: dict[str, list[str]] = {}
    for sp in speakers:
        label = sp.get("label")
        ids = sp.get("speaker_identifiers") or []
        if label:
            out[label] = list(ids)
    return out or None


def _extract_model(payload: dict) -> str | None:
    """Acoustic model echoed in metadata (identifiers are tied to it).
    Falls back to the deprecated operating_point."""
    cfg = (payload.get("metadata") or {}).get("transcription_config") or {}
    model = cfg.get("model") or cfg.get("operating_point")
    return str(model) if model else None
