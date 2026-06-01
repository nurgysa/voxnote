# tests/test_dedup_board_registry.py
import pytest

from tasks.backends.base import ExistingItem
from tasks.dedup import build_board_registry


class _FakeBackend:
    name = "linear"

    def __init__(self, items, *, raises=None):
        self._items = items
        self._raises = raises

    def list_existing(self, container_id):
        if self._raises:
            raise self._raises
        return self._items


def test_maps_existing_items_to_sent_tasks():
    backend = _FakeBackend([
        ExistingItem(title="Изучить систему СУП", ref="uuid-37",
                     identifier="NUR-37", url="http://x/37", description="desc"),
    ])
    reg = build_board_registry(backend, "team-1")
    assert len(reg) == 1
    s = reg[0]
    assert s.title == "Изучить систему СУП"
    assert s.backend == "linear"
    assert s.container_id == "team-1"
    assert s.ref == "uuid-37"
    assert s.identifier == "NUR-37"
    assert s.description == "desc"
    assert s.meeting_name == "" and s.meeting_date == ""


def test_skips_items_without_title_or_ref():
    backend = _FakeBackend([
        ExistingItem(title="", ref="r", identifier="x", url=""),
        ExistingItem(title="ok", ref="", identifier="x", url=""),
        ExistingItem(title="keep", ref="r2", identifier="NUR-2", url=""),
    ])
    reg = build_board_registry(backend, "team-1")
    assert [s.title for s in reg] == ["keep"]


def test_backend_error_propagates():
    backend = _FakeBackend([], raises=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        build_board_registry(backend, "team-1")
