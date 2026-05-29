"""Unit tests for the de-hardcoded backend-metadata dicts.

ui.dialogs.extract_tasks.constants imports only datetime (no sounddevice),
so it is safe to import directly on Linux CI — unlike the dialog package's
__init__.py which pulls in CTk widgets.
"""
from __future__ import annotations

from ui.dialogs.extract_tasks import constants as C


def test_name_to_display_covers_three_backends():
    assert C._NAME_TO_DISPLAY == {
        "linear": "Linear", "glide": "Glide", "trello": "Trello",
    }


def test_display_to_name_is_exact_inverse():
    assert C._DISPLAY_TO_NAME == {v: k for k, v in C._NAME_TO_DISPLAY.items()}


def test_cache_key_per_backend_distinct():
    keys = C._CACHE_KEY_BY_BACKEND
    assert keys["linear"] == C._TEAMS_CACHE_KEY
    assert keys["glide"] == C._BOARDS_CACHE_KEY
    assert keys["trello"] == C._TRELLO_CACHE_KEY
    # All three distinct so cached containers never collide.
    assert len(set(keys.values())) == 3


def test_container_label_header_includes_trello():
    assert C._CONTAINER_LABEL_BY_BACKEND["trello"] == "Список"


def test_empty_label_and_accusative_cover_trello():
    assert C._EMPTY_CONTAINER_LABEL_BY_BACKEND["trello"] == "(нет списков)"
    assert C._CONTAINER_ACCUSATIVE_BY_BACKEND["trello"] == "список"


def test_required_keys_trello_needs_both_credentials():
    assert C._REQUIRED_KEYS_BY_BACKEND["trello"] == ("trello_api_key", "trello_token")
    assert C._REQUIRED_KEYS_BY_BACKEND["linear"] == ("linear_api_key",)
    assert C._REQUIRED_KEYS_BY_BACKEND["glide"] == ("glide_api_key",)
