# tests/test_trello_client_list_cards.py
import pytest

from tasks.trello_client import TrelloClient, TrelloError


def _client():
    return TrelloClient(api_key="k", token="t")


def test_resolves_list_to_board_then_lists_open_cards(monkeypatch):
    c = _client()
    calls = []

    def fake_request(method, path, *, params=None, timeout=30.0):
        calls.append((method, path, params))
        if path == "/lists/list-1":
            return {"idBoard": "board-9"}
        if path == "/boards/board-9/cards":
            return [{"id": "card-1", "name": "Изучить СУП", "desc": "d",
                     "url": "http://c/1", "idShort": 5, "shortLink": "abc"}]
        raise AssertionError(path)

    monkeypatch.setattr(c, "_request", fake_request)
    cards = c.list_open_cards("list-1")
    assert [x["id"] for x in cards] == ["card-1"]
    # board-level fetch, open filter
    board_call = [x for x in calls if x[1] == "/boards/board-9/cards"][0]
    assert board_call[2]["filter"] == "open"


def test_raises_when_board_unresolvable(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_request", lambda m, p, *, params=None, timeout=30.0: {})
    with pytest.raises(TrelloError):
        c.list_open_cards("list-1")
