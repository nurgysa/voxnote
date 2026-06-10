"""config.example.json must not resurrect CUDA-era ghost keys.

The template is not just documentation: utils._seed_default_config copies
it into ~/.audio-transcriber/config.json on every fresh frozen install, so
dead keys here propagate into real user configs. These keys died with the
2026-05-28 cloud-only rip-out (#103 removed the last plumbing; the no-op
normalize toggle followed once its checkbox was removed);
test_bundle_ui_only.py guards the code side of the same regression.
"""
import json
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"

GHOST_KEYS = {
    "hf_token",
    "voices",
    "model",
    "transcribe_device",
    "diarize_device",
    "cloud_enabled",
    "normalize_audio",
}


def test_config_example_has_no_ghost_keys():
    keys = set(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    present = GHOST_KEYS & keys
    assert not present, f"CUDA-era ghost keys back in the template: {sorted(present)}"
