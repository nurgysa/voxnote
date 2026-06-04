"""Persistence-resilience tests for utils config/data writes (WS-3).

Covers the failure modes the audit flagged:
  * a corrupt config.json must NOT crash app start (it's quarantined, {} returned)
  * config writes must be atomic (tmp + os.replace, no stray .tmp)
  * best-effort per-run saves (segments/speakers) must NOT crash the
    post-transcription completion handler when the disk write fails.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_load_config_quarantines_corrupt_json(tmp_path: Path) -> None:
    """A present-but-invalid config.json must NOT crash app start — it is moved
    aside to config.json.corrupt-* and {} returned so the app launches and the
    first-run banner can recover."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{ not valid json ", encoding="utf-8")

    with patch("utils._CONFIG_PATH", str(config_path)):
        from utils import load_config
        result = load_config()

    assert result == {}
    assert not config_path.exists(), "corrupt file should be moved aside, not left in place"
    assert len(list(tmp_path.glob("config.json.corrupt-*"))) == 1, "bad file preserved for recovery"


def test_save_config_is_atomic_and_round_trips(tmp_path: Path) -> None:
    """save_config writes via a tmp sibling + os.replace; the live file is valid
    afterward and no stray .tmp is left behind."""
    config_path = tmp_path / "config.json"
    payload = {"language": "Русский", "cloud_api_keys": {"AssemblyAI": "k"}}
    with patch("utils._CONFIG_PATH", str(config_path)):
        from utils import load_config, save_config
        save_config(payload)
        assert load_config() == payload
    assert not (tmp_path / "config.json.tmp").exists()


def test_save_segments_swallows_write_failure(tmp_path: Path) -> None:
    """segments.json is a best-effort cache — a write failure (here, a missing
    target folder) must NOT raise into the completion handler."""
    from utils import save_segments
    missing = tmp_path / "does_not_exist"
    save_segments(str(missing), [{"start": 0.0, "end": 1.0, "text": "x"}])  # must not raise
    assert not (missing / "segments.json").exists()


def test_save_speakers_swallows_write_failure(tmp_path: Path) -> None:
    """speakers.json is a best-effort context cache — a write failure must not
    raise into the caller."""
    from utils import save_speakers
    missing = tmp_path / "does_not_exist"
    save_speakers(str(missing), project_id="p", participant_ids=["a"])  # must not raise
    assert not (missing / "speakers.json").exists()
