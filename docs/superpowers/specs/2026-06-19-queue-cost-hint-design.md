# Cost hint at enqueue — design

Date: 2026-06-19
Status: approved
Topic: deferred hygiene for the transcription queue (last item)

## Context

`processing/preflight.estimate_cost(provider, duration_s) -> float | None` already
computes a rough STT cost (per-hour with-diarization rates in `_COST_PER_HOUR`) and is
unit-tested (`tests/test_preflight.py`). It is **never surfaced in the UI** — the user
adds a file to the queue with no sense of what it will cost. This is the last of the
three deferred-hygiene items (DONE-pruning #160 and dismiss-ERROR #161 shipped).

The interactive enqueue path is `QueueMixin._enqueue(audio_path, source)`
(`ui/app/queue_mixin.py`), used by record-stop and «Выбрать файл». It already writes
«Добавлено в очередь: <name>» to `self._lbl_status`. The inbox auto-enqueue
(`_inbox_tick`) calls `self._queue.enqueue` directly and is **out of scope** — it is
automated, with no user watching to read a hint.

## Goal

When the user adds a file to the queue, append a rough cost estimate to the
confirmation status line, e.g. «Добавлено в очередь: rec.m4a · ~$0.12».

## Design

### New pure helper (`processing/preflight.py`)

```python
def cost_hint_suffix(provider: str, duration_s: float | None) -> str:
    """' · ~$X.XX' for an at-enqueue status-line hint, or '' when the cost is
    unknown (duration unmeasurable or provider not in the rate table)."""
    cost = estimate_cost(provider, duration_s)
    if cost is None:
        return ""
    return f" · ~${cost:.2f}"
```

Pure, headless, unit-tested. Distinct from the extract dialog's token-based
`estimate_cost_hint` (different module, signature, and purpose).

### Wiring (`ui/app/queue_mixin.py::_enqueue`)

After the API-key pre-check, probe the file for its duration and fold the suffix into
the existing status line:

```python
info = preflight.probe(audio_path)
hint = preflight.cost_hint_suffix(provider, info.get("duration_s"))
self._queue.enqueue(audio_path, self._build_options(source))
self._lbl_status.configure(
    text=f"Добавлено в очередь: {os.path.basename(audio_path)}{hint}",
    text_color=GREEN,
)
```

Adds `from processing import preflight` to the module imports.

### Behavior

- **Passive hint, no confirmation, no gate** (the user explicitly chose this). It never
  blocks an enqueue.
- **Graceful degradation:** unknown duration (soundfile can't read it *and* ffmpeg is
  absent / unparseable) → `estimate_cost` returns `None` → empty suffix → the status
  line is exactly today's «Добавлено в очередь: <name>».
- **Reflects the selected provider** — `_enqueue` already reads
  `provider = self._cloud_provider_var.get()` for its key-check; the hint uses the same.

### Probe cost

`probe` runs on the Tk thread during the button callback: `os.path.getsize` +
a soundfile header read (instant for WAV) or an `ffmpeg -i` header read (~100-300 ms for
`.m4a`/`.mp3`). A brief, one-time pause on an explicit "add to queue" click is
acceptable; no threading needed for a hint. (The worker re-probes later for the real
size/duration gate — a second sub-second probe is negligible.)

## Testing

Real unit tests (headless) for the pure helper, in `tests/test_preflight.py` beside the
existing `estimate_cost` tests:

- `cost_hint_suffix("AssemblyAI", 3600.0) == " · ~$0.17"` (formats 2 decimals)
- `cost_hint_suffix("AssemblyAI", None) == ""` (unknown duration)
- `cost_hint_suffix("Nope", 3600.0) == ""` (unknown provider)

Source-slice wiring assertion for `_enqueue` (importing `ui.app` pulls
sounddevice/PortAudio and crashes Linux CI), in a new
`tests/test_ui_cost_hint.py` reading `ui/app/queue_mixin.py` text:

- imports `preflight`; `_enqueue` calls `preflight.probe(` and `cost_hint_suffix(`; the
  status line interpolates the hint.

`pytest` green (baseline ≈ 1067) and `ruff` clean before commit.

## Out of scope

- A cost **gate**/confirmation for expensive jobs (this is a hint, not a guard).
- Per-item cost display in the «Встречи» rows.
- A hint on the automated inbox enqueue path.
