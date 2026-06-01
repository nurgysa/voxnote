# tests/test_trello_client_card_comments.py
from tasks.trello_client import TrelloClient


def test_list_card_comments_extracts_text(monkeypatch):
    c = TrelloClient(api_key="k", token="t")

    def fake_request(method, path, *, params=None, timeout=30.0):
        assert path == "/cards/card-1/actions"
        assert params["filter"] == "commentCard"
        return [
            {"data": {"text": "first"}},
            {"data": {"text": "second"}},
        ]

    monkeypatch.setattr(c, "_request", fake_request)
    assert c.list_card_comments("card-1") == ["first", "second"]


def test_list_card_comments_handles_nonlist(monkeypatch):
    c = TrelloClient(api_key="k", token="t")
    monkeypatch.setattr(c, "_request", lambda m, p, *, params=None, timeout=30.0: {})
    assert c.list_card_comments("card-1") == []
