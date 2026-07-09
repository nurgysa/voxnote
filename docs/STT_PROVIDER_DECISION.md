# STT provider decision for VoxNote

**Decision date:** 2026-07-09
**Status:** accepted direction, pending real A/B validation
**Scope:** cloud speech-to-text providers for VoxNote long-meeting intake

This document records the current provider decision for VoxNote after a Deep
Research pass and targeted verification against official provider docs/pricing.
It is a product and implementation decision, not a benchmark result.

## Context

VoxNote is the Mini-AGI voice/audio intake layer:

```text
audio / meeting recording
→ VoxNote transcription + diarization
→ transcript.md in Obsidian
→ raw audio archived under Drive Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/
→ optional audio.transcribed nudge to Hermes
→ Hermes downstream protocol/tasks/approval/GBrain enrichment
```

The STT provider must optimize for:

- long meetings: 60–180 minutes;
- Russian + Kazakh + English, often code-switching inside one recording;
- speaker diarization / speaker turns;
- Python/headless/desktop API fit;
- predictable cost and no silent auto-retry for expensive jobs;
- privacy controls suitable for business meetings.

VoxNote owns transcription and diarization only. Hermes owns downstream
interpretation, protocol, tasks, approval gates, tracker sends, and memory/GBrain
enrichment.

## Executive decision

| Role | Provider | Decision |
|---|---|---|
| Default provider | AssemblyAI | Use as the default VoxNote provider. |
| KZ/RU/EN fallback | Gladia | Keep as fallback, especially for KZ-heavy mixed-language audio, with chunking for long files. |
| EN/RU cheap/test-only | Speechmatics | Optional future cheap mode for EN/RU-only workflows, not for core VoxNote. |
| Not primary | Deepgram | Good API, but not a fit for core VoxNote because Kazakh is not publicly listed as supported. |

## Important correction to the Deep Research report

The high-level recommendation “AssemblyAI default” is accepted, but one detail
must be corrected:

```text
Do not claim that AssemblyAI Universal-3.5 Pro directly covers Kazakh.
```

Official AssemblyAI supported-language documentation says Universal-3.5 Pro
supports 18 languages. Kazakh is not in that 18-language list. Kazakh is listed
under Universal-2 and is grouped as moderate accuracy.

Therefore the correct VoxNote stance is:

```text
AssemblyAI as a provider covers the VoxNote language profile via model routing:
Universal-3.5 Pro for its supported languages
→ Universal-2 fallback for Kazakh / unsupported-language cases.
```

Recommended AssemblyAI configuration concept:

```yaml
provider: AssemblyAI
speech_models:
  - universal-3-5-pro
  - universal-2
language_detection: true
speaker_labels: true
```

Implementation must verify the exact AssemblyAI SDK/API parameter names before
coding. The important product requirement is provider-level model fallback, not a
single Universal-3.5-only path.

## Provider matrix

| Provider | RU | KZ | EN | Code-switching | Diarization | Long audio | Cost fit | Verdict |
|---|---|---|---|---|---|---|---|---|
| AssemblyAI | Strong | Via Universal-2, moderate | Strong | Strong with model routing | Yes | Good | Good | Default provider. |
| Gladia | Yes | Yes | Yes | Yes | Yes | Partial: 135 min cap on normal pre-recorded jobs | Medium | Fallback with chunking for >135 min. |
| Deepgram | Yes | Not publicly listed | Yes | Good for supported languages | Yes | Good | Medium | Not primary for VoxNote. |
| Speechmatics | Yes | Not publicly listed | Yes | Good for supported languages | Yes | Good | Strong | Cheap EN/RU-only future option. |

## ASR-only / no-speaker-label mode

VoxNote now distinguishes two queue modes:

```yaml
transcription_mode: meeting    # request diarization / speaker labels
transcription_mode: asr_only   # no speaker labels, no speaker-count hints, no Voice-ID
```

Use `asr_only` for fast notes, lectures, cheap previews, and KZ-heavy ASR
benchmarks where speaker turns are not required. This mode keeps the same durable
`transcript.md` output contract but deliberately avoids diarization-specific API
features and downstream speaker sidecars.

Recommended no-diarization evaluation order:

1. Groq `whisper-large-v3-turbo` — cheapest/fastest ASR-only baseline.
2. Together `openai/whisper-large-v3` — strongest hosted-Whisper long-form
   candidate; first serious Whisper-family A/B target.
3. Groq `whisper-large-v3` — quality-oriented Whisper benchmark if turbo is weak.
4. OpenAI `gpt-4o-mini-transcribe` / `gpt-4o-transcribe` — OpenAI text-only
   baselines for shorter files or external chunking experiments.
5. Fireworks Whisper v3 — optional cheap hosted-Whisper benchmark.

Do not let this mode dilute the production meeting decision: when speaker labels
matter, AssemblyAI default + Gladia fallback remain the primary route.

## AssemblyAI decision notes

Why default:

- Good match for async long-meeting transcription.
- Strong published pricing for Universal-3.5 Pro plus diarization.
- Supports model routing / fallback for broader language coverage.
- Universal-2 includes Kazakh in official language documentation.
- Speaker diarization is available as an add-on.
- Privacy/data controls are better aligned with business-meeting workflows than
  ad-hoc provider usage.

Risks / caveats:

- Kazakh should be treated as Universal-2 fallback, not Universal-3.5 Pro direct
  support.
- AssemblyAI docs classify Kazakh Universal-2 accuracy as moderate, so real
  KZ-heavy recordings need validation.
- Some privacy controls are plan-dependent and may apply only to future requests.
- Diarization is an add-on cost.

VoxNote implication:

- Make AssemblyAI the default provider, but expose model/fallback metadata in
  `transcript.md` so operators can see whether fallback was used.

## Gladia decision notes

Why fallback:

- Official docs list Kazakh (`kk`), Russian (`ru`), and English (`en`).
- Supports code switching with `language_config.languages`, e.g. `["ru", "kk", "en"]`.
- Solaria-1 is the right model family for maximum language coverage and
  code-switching.
- API flow is suitable for upload/create/poll/callback.

Risks / caveats:

- Normal pre-recorded jobs have a documented 135-minute maximum duration.
- Enterprise extends duration, but VoxNote should not assume enterprise limits.
- For >135-minute files, VoxNote must chunk 45–60 minutes with stitching.
- Zero-retention / stricter privacy modes can change the API flow and may require
  external URLs and callbacks.

VoxNote implication:

```text
if provider == Gladia and duration > 135 minutes:
    require chunking before submit
```

Gladia should not silently receive 180-minute single-file jobs.

## Deepgram decision notes

Why not primary:

- Deepgram has a strong API and mature diarization surface.
- However, Kazakh is not publicly listed in the official supported-language docs
  checked for this decision.
- VoxNote’s core use case includes RU/KZ/EN code-switching, so missing Kazakh is
  a structural mismatch.

Possible future role:

- English-only or Russian-heavy secondary route if a future benchmark proves it
  useful and the product adds language-aware routing.

## Speechmatics decision notes

Why not primary:

- Speechmatics has a strong batch API and attractive public pricing.
- However, Kazakh is not publicly listed in the official supported-language docs
  checked for this decision.
- That makes it unsuitable as a core VoxNote provider for KZ/RU/EN meetings.

Possible future role:

- Cheap test mode for EN/RU-only workflows.
- Do not use it for KZ validation unless official Kazakh support appears.

## Configuration direction

Current generic env shape:

```text
VOXNOTE_PROVIDER
VOXNOTE_API_KEY
```

Recommended production-ready shape:

```text
VOXNOTE_PROVIDER=AssemblyAI
VOXNOTE_ASSEMBLYAI_API_KEY=...
VOXNOTE_GLADIA_API_KEY=...
VOXNOTE_DEEPGRAM_API_KEY=...
VOXNOTE_SPEECHMATICS_API_KEY=...
```

Keep `VOXNOTE_API_KEY` as a legacy fallback for the active provider, but prefer
provider-specific secrets for real multi-provider routing. This avoids swapping a
single key when provider changes and allows fallback providers to be ready.

## Required transcript metadata

Each `transcript.md` should persist enough provenance to debug quality, cost, and
fallback behavior:

```yaml
provider: assemblyai
model_primary: universal-3-5-pro
model_fallback: universal-2
fallback_used: false
language_mode: mixed
languages_expected: [ru, kk, en]
diarization: true
duration_sec: 0
submitted_at: null
completed_at: null
provider_job_id: null
source_path: null
source_sha256: null
cost_estimate_usd: null
confidence_summary: null
speakers_detected: null
chunked: false
provider_raw_response_path: null
provider_request_payload_path: null
poll_attempts: 0
fallback_from: null
fallback_reason: null
```

For Gladia chunking, also persist:

```yaml
chunked: true
chunk_duration_sec: 3600
chunk_overlap_sec: 30
chunks:
  - index: 1
    provider_job_id: ...
    start_sec: 0
    end_sec: 3600
```

## Preflight policy for long meetings

Before any expensive upload/submission:

1. Determine duration and file size.
2. Estimate provider cost.
3. Confirm provider and model route.
4. If expected languages include Kazakh, avoid Deepgram/Speechmatics as core providers.
5. If provider is Gladia and duration is over 135 minutes, require chunking.
6. Never silently auto-retry failed long jobs.
7. Persist local job state before submit so retries are idempotent.
8. Write final `transcript.md` only after terminal provider state.

## Validation plan

This decision is not final until real VoxNote audio is tested.

Run a blind A/B validation:

| Bucket | Recordings | Goal |
|---|---:|---|
| RU/EN, 60–90 min | 3–4 | Baseline long-meeting quality. |
| RU/KZ/EN, 90–135 min | 3–4 | Code-switching and diarization stress. |
| RU/KZ/EN noisy/overlap | 3–4 | Realistic business-meeting failure modes. |
| 135–180 min | 2–4 | Long-file path and Gladia chunking behavior. |

Compare:

- speaker turn boundaries;
- stable speaker labels after pauses;
- RU/KZ boundary correctness;
- project names and jargon;
- transcript.md readability;
- downstream usefulness for Hermes protocol/tasks;
- provider failure/retry rate;
- cost per usable transcript.

### Whisper-family / OpenAI evaluation candidate

Do not promote raw Whisper large-v3 to primary provider by default. It is a strong
ASR baseline and includes Kazakh (`kk`) in the official Hugging Face language
list, but it does not provide robust native meeting diarization by itself.

Evaluate it as a benchmark/fallback candidate only:

- Together `openai/whisper-large-v3`: strongest hosted-Whisper candidate found
  in the second Deep Research pass. Official/public pages describe a real cloud
  STT API around Whisper large-v3, long-file handling, word timestamps, and
  diarization-related features. Treat it as the first Whisper-family A/B
  candidate, but do not promote it without real VoxNote RU/KZ/EN meeting tests.
- Groq Whisper large-v3 / large-v3-turbo: cheap and fast transcribe-only
  benchmark. Groq turbo is especially attractive for low-cost ASR, but it lacks
  a documented native speaker-label contract, so it is not meeting-primary.
- OpenAI `gpt-4o-transcribe-diarize`: separate evaluation-only diarization
  benchmark. It is interesting for speaker-aware output, but file-size/chunking
  constraints make it an orchestration project for 60-180 minute meetings.
- Fireworks Whisper v3: cheap hosted Whisper benchmark/transcribe-only candidate;
  verify native diarization and long-file behavior before any implementation.
- Replicate / Hugging Face Endpoints / self-hosted Whisper: experiments only.
  They add too much deployment or diarization/merging burden for VoxNote's
  production cloud-only path.

Acceptance bar: any Whisper-family route must beat or materially complement
AssemblyAI/Gladia on real VoxNote meeting audio without adding unacceptable
chunking, diarization, or ops burden.

## Next implementation tasks

1. [Done 2026-07-09] Add provider-specific API key resolution while keeping `VOXNOTE_API_KEY` as
   legacy fallback.
2. Add AssemblyAI model routing metadata and verify exact SDK/API params.
3. Add transcript provenance metadata schema.
4. Add preflight cost/duration checks.
5. Add Gladia duration guard and chunking requirement for >135 minutes.
6. Add a provider A/B test fixture plan with sanitized real recordings.
7. Include Together `openai/whisper-large-v3`, Groq turbo, and OpenAI
   `gpt-4o-transcribe-diarize` in the evaluation matrix without changing the
   AssemblyAI/Gladia production default.
8. Update user/operator docs: AssemblyAI default, Gladia fallback, Deepgram and
   Speechmatics non-primary because of Kazakh coverage.

## Official sources checked

- AssemblyAI pricing: https://www.assemblyai.com/pricing
- AssemblyAI code switching docs: https://www.assemblyai.com/docs/pre-recorded-audio/code-switching
- AssemblyAI supported languages docs: https://www.assemblyai.com/docs/getting-started/supported-languages
- Gladia supported file duration docs: https://docs.gladia.io/chapters/limits-and-specifications/supported-formats
- Gladia supported languages docs: https://docs.gladia.io/chapters/language/supported-languages
- Gladia code switching docs: https://docs.gladia.io/chapters/language/code-switching
- Gladia pre-recorded quickstart: https://docs.gladia.io/chapters/pre-recorded-stt/quickstart
- Deepgram language docs: https://developers.deepgram.com/docs/language
- Speechmatics language docs: https://docs.speechmatics.com/speech-to-text/languages
- OpenAI Whisper large-v3 model card: https://huggingface.co/openai/whisper-large-v3
- OpenAI speech-to-text docs: https://developers.openai.com/api/docs/guides/speech-to-text
- Groq speech-to-text docs: https://console.groq.com/docs/speech-to-text
- Groq Whisper large-v3 docs: https://console.groq.com/docs/model/whisper-large-v3
- Groq Whisper large-v3 Turbo docs: https://console.groq.com/docs/model/whisper-large-v3-turbo
- Together speech-to-text docs: https://docs.together.ai/docs/speech-to-text
- Together Whisper large-v3 model page: https://www.together.ai/models/openai-whisper-large-v3
- Together pricing: https://www.together.ai/pricing
- Fireworks Whisper v3 model page: https://app.fireworks.ai/models/fireworks/whisper-v3

## Final decision

Use AssemblyAI as the default VoxNote STT provider, with model fallback to
Universal-2 for Kazakh/unsupported-language cases. Keep Gladia as the KZ-capable
fallback, but require chunking for files over the normal 135-minute pre-recorded
limit. Do not prioritize Deepgram or Speechmatics for the core VoxNote path until
they publicly support Kazakh or the product adds EN/RU-only routing modes.
