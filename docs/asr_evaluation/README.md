# Stage‑0 ASR evaluation

This directory contains a local, provider-neutral comparison workflow for
VoxNote ASR-only experiments. It makes **no network calls** and never reads API
keys, audio paths, or provider configuration.

## Purpose

Use this after manually preparing a small human-verified corpus to compare
provider outputs or preprocessing variants. The report computes deterministic
word-error statistics, key-term recall, segment availability, wall-clock time,
and supplied cost values. It does **not** claim semantic quality or timestamp
accuracy automatically: those require human review against the original audio.

## Stage‑0 categories

Start with clips from these six categories:

1. clean Russian speech;
2. quiet Russian speech after a loudness-rescue candidate;
3. Kazakh speech;
4. English speech;
5. Kazakh–Russian code-switching;
6. noisy or far-field speech.

The example manifest contains synthetic text only. Keep real meeting audio and
human gold transcripts outside version control unless their sharing terms have
been reviewed.

## Contracts

The corpus manifest and provider result files are JSON with `schema_version: 1`.

Manifest items require:

```json
{
  "id": "ru-clean-001",
  "gold_text": "Human-verified reference text",
  "languages": ["ru"],
  "tags": ["ru-clean"],
  "key_terms": ["optional domain term"]
}
```

Provider result items require:

```json
{
  "id": "ru-clean-001",
  "text": "Provider transcript",
  "wall_clock_s": 12.3,
  "cost_usd": 0.01,
  "segments": [{"start": 0.0, "end": 1.2, "text": "optional segment"}]
}
```

The result root also requires `schema_version`, `provider`, `model`, and
`items`. Every corpus ID must appear exactly once in the result.

## Run locally

```bash
python scripts/asr_stage0_evaluate.py \
  --manifest docs/asr_evaluation/stage0-manifest.example.json \
  --result docs/asr_evaluation/example_result.json \
  --out-json /tmp/asr-report.json \
  --out-md /tmp/asr-report.md
```

The CLI writes Markdown to stdout and optionally persists JSON/Markdown report
files. It does not transcribe audio; provider results must be collected by a
separate human-approved A/B run.

## Interpretation

Use WER only where `gold_text` is human-verified. Evaluate names, terms,
semantic preservation, hallucinations, timestamp drift, language fidelity,
latency, and reliability separately in the review sheet. Do not select a
provider solely by character count or aggregate WER.
