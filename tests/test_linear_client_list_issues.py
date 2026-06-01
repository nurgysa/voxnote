# tests/test_linear_client_list_issues.py
from tasks.linear_client import LinearClient


def _client():
    return LinearClient(api_key="k")


def test_single_page(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_graphql", lambda q, v=None: {
        "team": {"issues": {
            "nodes": [{"id": "i1", "identifier": "NUR-1", "title": "T1",
                       "url": "u1", "description": "d1"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}
    })
    issues = c.list_issues("team-1")
    assert [i["identifier"] for i in issues] == ["NUR-1"]


def test_multi_page_follows_cursor(monkeypatch):
    c = _client()
    node1 = {"id": "i1", "identifier": "NUR-1", "title": "T1", "url": "", "description": ""}
    node2 = {"id": "i2", "identifier": "NUR-2", "title": "T2", "url": "", "description": ""}
    pages = [
        {"team": {"issues": {
            "nodes": [node1],
            "pageInfo": {"hasNextPage": True, "endCursor": "CUR"}}}},
        {"team": {"issues": {
            "nodes": [node2],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}},
    ]
    seen_cursors = []

    def fake_graphql(q, v=None):
        seen_cursors.append((v or {}).get("after"))
        return pages.pop(0)

    monkeypatch.setattr(c, "_graphql", fake_graphql)
    issues = c.list_issues("team-1")
    assert [i["identifier"] for i in issues] == ["NUR-1", "NUR-2"]
    assert seen_cursors == [None, "CUR"]


def test_empty_team(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_graphql", lambda q, v=None: {"team": {"issues": {
        "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
    assert c.list_issues("team-1") == []


def test_query_excludes_completed_and_canceled():
    from tasks.linear_client import _TEAM_ISSUES_QUERY
    assert '"completed"' in _TEAM_ISSUES_QUERY
    assert '"canceled"' in _TEAM_ISSUES_QUERY
    assert "nin" in _TEAM_ISSUES_QUERY
