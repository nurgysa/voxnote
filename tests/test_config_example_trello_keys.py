"""config.example.json must document the Trello backend keys."""
from __future__ import annotations

import json
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def test_config_example_has_trello_keys():
    data = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    assert "trello_api_key" in data
    assert "trello_token" in data
    assert "trello_enabled" in data


def test_trello_enabled_defaults_false_opt_in():
    """Spec D5: Trello is opt-in (unlike linear/glide which default true)."""
    data = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    assert data["trello_enabled"] is False
    assert data["trello_api_key"] == ""
    assert data["trello_token"] == ""
