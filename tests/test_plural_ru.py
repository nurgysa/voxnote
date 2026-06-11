"""Russian plural word-form helper (spec 2026-06-11, PR-1)."""
import pytest

from utils import plural_ru


@pytest.mark.parametrize("n,expected", [
    (0, "встреч"),
    (1, "встреча"),
    (2, "встречи"),
    (4, "встречи"),
    (5, "встреч"),
    (10, "встреч"),
    (11, "встреч"),     # 11–14 are always the many-form…
    (12, "встреч"),
    (14, "встреч"),
    (21, "встреча"),    # …but 21/121 go back to the one-form
    (22, "встречи"),
    (25, "встреч"),
    (100, "встреч"),
    (101, "встреча"),
    (111, "встреч"),    # 111 is the 11-exception at the next hundred
    (121, "встреча"),
])
def test_plural_ru_meetings(n, expected):
    assert plural_ru(n, "встреча", "встречи", "встреч") == expected
