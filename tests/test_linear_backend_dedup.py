# tests/test_linear_backend_dedup.py
from tasks.backends.base import ExistingItem
from tasks.backends.linear import LinearBackend


class _FakeClient:
    def __init__(self):
        self.issues = [
            {"id": "u37", "identifier": "NUR-37", "title": "Изучить систему СУП",
             "url": "http://x/37", "description": "desc37"},
        ]
        self.comments = ["nope", "yes <!-- audiotx-dedup:abc123def456 -->"]

    def list_issues(self, team_id):
        return self.issues

    def list_comments(self, issue_id):
        return self.comments


def test_list_existing_maps_to_existing_item():
    b = LinearBackend(_FakeClient())
    items = b.list_existing("team-1")
    assert items == [ExistingItem(
        title="Изучить систему СУП", ref="u37", identifier="NUR-37",
        url="http://x/37", description="desc37",
    )]


def test_comment_exists_substring_match():
    b = LinearBackend(_FakeClient())
    assert b.comment_exists("u37", "<!-- audiotx-dedup:abc123def456 -->") is True
    assert b.comment_exists("u37", "<!-- audiotx-dedup:zzz -->") is False
