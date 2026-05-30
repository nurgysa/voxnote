# Task-Dedup PR-1 — Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Thread a comment-addressable backend id (`ref`) from `create()` to `tasks.json`, and give each backend an `add_comment()` + `supports_comments` capability — the foundation the dedup engine (PR-2) and UI (PR-3) build on. **No behaviour change yet.**

**Architecture:** `CreatedIssue.ref` (already stubbed) is populated by each adapter's `create()` (Linear node-UUID `issue["id"]`, Trello full `card["id"]`); the sender copies it into `Task.backend_ref` (persisted via the existing `to_dict`/`from_dict`). New `add_comment()` on the Linear/Trello clients + adapters posts a comment to that id; Glide opts out (`supports_comments=False`).

**Tech Stack:** Python 3.10+, `requests` (HTTP, mocked via `unittest.mock.patch.object`), pytest, ruff. No new deps.

**Design source:** `~/.claude/plans/foamy-wobbling-owl.md` (PR-1 section); decisions also in memory `project-task-dedup`.

**Branch:** `feat/task-dedup-foundation` (off updated `main`). The uncommitted `tasks/backends/base.py` + `tasks/schema.py` WIP (contract stubs) is already in the working tree — Task 1 commits it. Do NOT stage `cli/` or `tests/test_cli_import_guard.py` (separate WIP).

---

## File Structure

| File | Change |
|------|--------|
| `tasks/backends/base.py` | **(WIP, commit in T1)** `CreatedIssue.ref`, `TaskBackend.supports_comments`, `add_comment()` Protocol |
| `tasks/schema.py` | **(WIP, commit in T1)** `Task.backend_ref` + to_dict/from_dict |
| `tasks/linear_client.py` | + `add_comment(issue_id, body)` (GraphQL `commentCreate`) |
| `tasks/trello_client.py` | + `add_comment(card_id, text)` (`POST /cards/{id}/actions/comments`) |
| `tasks/backends/linear.py` | `create()` sets `ref=issue["id"]`; `add_comment` delegate; `supports_comments=True` |
| `tasks/backends/trello.py` | `create()` sets `ref=card["id"]`; `add_comment` delegate; `supports_comments=True` |
| `tasks/backends/glide.py` | `supports_comments=False`; `add_comment` → `NotImplementedError` |
| `tasks/sender.py` | after success: `task.backend_ref = issue.ref` |
| `tests/test_tasks_schema.py` | backend_ref round-trip |
| `tests/test_tasks_linear_client.py` | `add_comment` success + failure |
| `tests/test_tasks_trello_client.py` | `add_comment` endpoint + params |
| `tests/test_tasks_backends.py` | per-adapter `ref`, `supports_comments`, `add_comment` delegate, Glide `NotImplementedError` |
| `tests/test_tasks_send.py` | `backend_ref` populated from `issue.ref` |

**Baseline:** run `python -m pytest` once at the start to record the green baseline; `ruff check .` clean. Both must pass before every commit. (Note: the working tree carries the uncommitted base.py/schema.py stubs — the suite should already be green with them.)

---

## Task 1: Commit the contract stubs + schema round-trip test

The `base.py` (`CreatedIssue.ref="" `, `supports_comments=False`, `add_comment(...)` Protocol) and `schema.py` (`Task.backend_ref`) changes already exist uncommitted. Lock the schema behaviour with a test, then commit both.

**Files:** Modify `tests/test_tasks_schema.py`; commit `tasks/backends/base.py` + `tasks/schema.py`.

- [ ] **Step 1: Write the round-trip test**

Append to `tests/test_tasks_schema.py` (match the existing import of `Task` there):

```python
def test_task_backend_ref_round_trips():
    t = Task(local_id="x1", title="t", backend_ref="ISSUE-UUID-123")
    assert t.to_dict()["backend_ref"] == "ISSUE-UUID-123"
    assert Task.from_dict(t.to_dict()).backend_ref == "ISSUE-UUID-123"


def test_task_backend_ref_defaults_none_and_tolerates_old_dict():
    assert Task(local_id="x2", title="t").backend_ref is None
    # old tasks.json (pre-feature) has no backend_ref key
    revived = Task.from_dict({"local_id": "x3", "title": "t"})
    assert revived.backend_ref is None
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_tasks_schema.py -k backend_ref -v`
Expected: PASS (the field already exists in the WIP). If it fails, the WIP stub is missing — STOP and report.

- [ ] **Step 3: Commit the contract + test**

```bash
git add tasks/backends/base.py tasks/schema.py tests/test_tasks_schema.py
git commit -m "feat(tasks): backend_ref + add_comment/supports_comments contract"  # + Co-Authored-By trailer
```
(Do NOT `git add -A` — `cli/` and `tests/test_cli_import_guard.py` are unrelated WIP.)

---

## Task 2: `LinearClient.add_comment`

**Files:** Modify `tasks/linear_client.py`; Test `tests/test_tasks_linear_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tasks_linear_client.py` (it already imports `LinearClient, LinearError` and `MagicMock, patch`):

```python
def test_add_comment_success():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"commentCreate": {"success": True}}}
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake) as mock_post:
        c.add_comment("issue-uuid-1", "снова обсуждалось")
    mock_post.assert_called_once()
    sent = mock_post.call_args.kwargs["json"]["variables"]
    assert sent == {"issueId": "issue-uuid-1", "body": "снова обсуждалось"}


def test_add_comment_raises_when_success_false():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"commentCreate": {"success": False}}}
    c = LinearClient("lin_api_test")
    with patch.object(c._session, "post", return_value=fake):
        with pytest.raises(LinearError, match="комментар"):
            c.add_comment("issue-uuid-1", "x")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_linear_client.py -k add_comment -v`
Expected: FAIL — `AttributeError: 'LinearClient' object has no attribute 'add_comment'`.

- [ ] **Step 3: Implement**

In `tasks/linear_client.py`, add the mutation constant after `_CREATE_ISSUE_MUTATION` (~line 56):

```python
_CREATE_COMMENT_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}
"""
```

Add the method to `LinearClient` (after `create_issue`):

```python
    def add_comment(self, issue_id: str, body: str) -> None:
        """Post a comment to an existing issue (task-dedup).

        ``issue_id`` is the node UUID (``issue["id"]``), NOT the ENG-123
        identifier — commentCreate rejects the human identifier. Raises
        LinearError on success=false or any HTTP/network failure.
        """
        data = self._graphql(
            _CREATE_COMMENT_MUTATION, {"issueId": issue_id, "body": body},
        )
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise LinearError(f"Linear отказался добавить комментарий: {result}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_linear_client.py -v` → Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tasks/linear_client.py tests/test_tasks_linear_client.py
git commit -m "feat(linear): add_comment via commentCreate mutation"  # + trailer
```

---

## Task 3: `TrelloClient.add_comment`

**Files:** Modify `tasks/trello_client.py`; Test `tests/test_tasks_trello_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tasks_trello_client.py` (reuse its `_resp` helper + `TrelloClient, TrelloError`):

```python
def test_add_comment_posts_to_actions_comments():
    c = TrelloClient("k", "t")
    with patch.object(
        c._session, "request",
        return_value=_resp(200, json_body={"id": "comment-1"}),
    ) as mock_req:
        c.add_comment("card-id-9", "снова обсуждалось")
    method, url = mock_req.call_args.args[0], mock_req.call_args.args[1]
    assert method == "POST"
    assert url.endswith("/cards/card-id-9/actions/comments")
    assert mock_req.call_args.kwargs["params"]["text"] == "снова обсуждалось"


def test_add_comment_rejects_empty_card_id():
    c = TrelloClient("k", "t")
    with pytest.raises(TrelloError, match="card_id"):
        c.add_comment("", "x")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_trello_client.py -k add_comment -v`
Expected: FAIL — no `add_comment` attribute.

- [ ] **Step 3: Implement**

Add to `TrelloClient` (after `create_card`):

```python
    def add_comment(self, card_id: str, text: str) -> None:
        """Post a comment to an existing card (task-dedup).

        ``card_id`` is the full card id (``card["id"]``), NOT the #idShort
        badge value. Raises TrelloError on network/HTTP failure.
        """
        if not card_id:
            raise TrelloError("card_id обязателен для add_comment")
        self._request(
            "POST", f"/cards/{card_id}/actions/comments", params={"text": text},
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_trello_client.py -v` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tasks/trello_client.py tests/test_tasks_trello_client.py
git commit -m "feat(trello): add_comment via POST /cards/{id}/actions/comments"  # + trailer
```

---

## Task 4: Adapters — `ref`, `supports_comments`, `add_comment`

**Files:** Modify `tasks/backends/{linear,trello,glide}.py`; Test `tests/test_tasks_backends.py`

- [ ] **Step 1: Write the failing tests**

Read the existing `tests/test_tasks_backends.py` first to match its fixture/stub-client style (the adapters take a client in `__init__`; tests pass a stub/MagicMock). Append tests asserting, for each adapter:

```python
def test_linear_create_sets_ref_to_node_uuid():
    client = MagicMock()
    client.create_issue.return_value = {
        "id": "node-uuid-1", "identifier": "ENG-1", "url": "http://x/ENG-1",
    }
    from tasks.backends.linear import LinearBackend
    issue = LinearBackend(client).create("team-1", Task(local_id="a", title="t"))
    assert issue.ref == "node-uuid-1"
    assert issue.identifier == "ENG-1"


def test_linear_supports_comments_and_delegates():
    client = MagicMock()
    from tasks.backends.linear import LinearBackend
    b = LinearBackend(client)
    assert b.supports_comments is True
    b.add_comment("node-uuid-1", "body")
    client.add_comment.assert_called_once_with("node-uuid-1", "body")


def test_trello_create_sets_ref_to_full_card_id():
    client = MagicMock()
    client.create_card.return_value = {
        "id": "card-full-id", "idShort": 7, "url": "http://x/7",
    }
    from tasks.backends.trello import TrelloBackend
    issue = TrelloBackend(client).create("list-1", Task(local_id="a", title="t"))
    assert issue.ref == "card-full-id"
    assert issue.identifier == "#7"


def test_trello_supports_comments_and_delegates():
    client = MagicMock()
    from tasks.backends.trello import TrelloBackend
    b = TrelloBackend(client)
    assert b.supports_comments is True
    b.add_comment("card-full-id", "body")
    client.add_comment.assert_called_once_with("card-full-id", "body")


def test_glide_opts_out_of_comments():
    from tasks.backends.glide import GlideBackend
    b = GlideBackend(MagicMock())
    assert b.supports_comments is False
    with pytest.raises(NotImplementedError):
        b.add_comment("ref", "body")
```

Ensure `from unittest.mock import MagicMock`, `import pytest`, and `from tasks.schema import Task` are imported (add if absent).

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tasks_backends.py -k "ref or supports_comments or opts_out or delegates" -v`
Expected: FAIL (ref is `""`; `supports_comments` not set / inherited False; `add_comment` not implemented on adapters).

- [ ] **Step 3: Implement**

`tasks/backends/linear.py` — set `supports_comments = True` (class attr, after `display_name`); in `create()` change the return to include `ref`; add `add_comment`:

```python
    name = "linear"
    display_name = "Linear"
    supports_comments = True
```
```python
        issue = self._client.create_issue(**kwargs)
        return CreatedIssue(
            identifier=issue.get("identifier") or "?",
            url=issue.get("url") or "",
            ref=issue.get("id") or "",
        )

    def add_comment(self, ref: str, body: str) -> None:
        self._client.add_comment(ref, body)
```

`tasks/backends/trello.py` — `supports_comments = True`; in `create()` return add `ref=card.get("id") or ""`; add `add_comment`:

```python
    name = "trello"
    display_name = "Trello"
    supports_comments = True
```
```python
        return CreatedIssue(
            identifier=identifier, url=card.get("url") or "",
            ref=card.get("id") or "",
        )

    def add_comment(self, ref: str, body: str) -> None:
        self._client.add_comment(ref, body)
```

`tasks/backends/glide.py` — `supports_comments = False`; `add_comment` raises:

```python
    name = "glide"
    display_name = "Glide"
    supports_comments = False
```
```python
    def add_comment(self, ref: str, body: str) -> None:
        # Glide has no comment API — the dedup gate never calls this
        # (supports_comments=False), but guard explicitly.
        raise NotImplementedError("Glide does not support comments")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_backends.py -v` → Expected: PASS (all, incl. pre-existing).

- [ ] **Step 5: Commit**

```bash
git add tasks/backends/linear.py tasks/backends/trello.py tasks/backends/glide.py tests/test_tasks_backends.py
git commit -m "feat(backends): populate ref + add_comment/supports_comments per adapter"  # + trailer
```

---

## Task 5: Sender writes `backend_ref`

**Files:** Modify `tasks/sender.py`; Test `tests/test_tasks_send.py`

- [ ] **Step 1: Write the failing test**

Read `tests/test_tasks_send.py` first to match its stub-backend pattern (it injects a `backend` with `.create()`; tasks are `Task`). Append:

```python
def test_send_populates_backend_ref_from_created_issue(...):
    # follow the file's existing pattern: a stub backend whose create()
    # returns a CreatedIssue with ref set, driven through send_tasks_iter.
    # Assert the sent task's backend_ref == that ref.
    ...
```

Concretely, mirror an existing success-path test in that file but make the stub's `create` return `CreatedIssue(identifier="ENG-1", url="u", ref="node-uuid-1")` and assert `task.backend_ref == "node-uuid-1"` after iterating `send_tasks_iter`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tasks_send.py -k backend_ref -v`
Expected: FAIL — `backend_ref` is `None` (sender doesn't set it yet).

- [ ] **Step 3: Implement**

In `tasks/sender.py`, in the success block (currently sets `task.linear_issue_id`/`url` ~line 102-107), add one line:

```python
        task.status = TaskStatus.SENT
        task.linear_issue_id = issue.identifier
        task.linear_issue_url = issue.url
        task.backend_ref = issue.ref
        task.send_error = None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tasks_send.py -v` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tasks/sender.py tests/test_tasks_send.py
git commit -m "feat(sender): persist backend_ref from CreatedIssue.ref"  # + trailer
```

---

## Task 6: Full-suite gate + finish

**Files:** none (verification).

- [ ] **Step 1:** `python -m pytest` → all green (baseline + new). Investigate any failure before proceeding.
- [ ] **Step 2:** `python -m ruff check .` → clean.
- [ ] **Step 3:** `git status` → confirm `cli/` + `tests/test_cli_import_guard.py` remain unstaged.
- [ ] **Step 4:** Finish via `superpowers:finishing-a-development-branch` → push `feat/task-dedup-foundation`, open PR against `main`. PR body: Summary + Test plan; note this is **PR-1 of 3** (foundation, no behaviour change); PR-2 (dedup engine) + PR-3 (UI) follow per `~/.claude/plans/foamy-wobbling-owl.md`. No manual GUI smoke needed (no UI in this PR).

---

## Self-Review

**Spec coverage (vs foamy-wobbling-owl PR-1):** CreatedIssue.ref ✓(T1) · supports_comments + add_comment Protocol ✓(T1) · Task.backend_ref + tolerant to_dict/from_dict ✓(T1) · sender writes backend_ref ✓(T5) · linear_client.add_comment ✓(T2) · trello_client.add_comment ✓(T3) · adapters ref+delegate+flag ✓(T4) · Glide opt-out ✓(T4) · tests for all ✓.

**Placeholder scan:** T5 Step 1 is described, not code-complete, because it must mirror the file's existing stub-backend fixture (read-first). The implementer is instructed to follow the existing success-path test pattern — acceptable since the exact fixture shape lives in that file. All other steps are code-complete.

**Type/name consistency:** `ref` (CreatedIssue) → `backend_ref` (Task) consistent; `add_comment(ref/issue_id/card_id, body/text)` — adapters use `(ref, body)`, clients use `(issue_id, body)`/`(card_id, text)`, delegation passes positionally. `supports_comments` class attr on all three adapters.

**Out of scope (do NOT touch):** `tasks/dedup.py` (PR-2), the Extract dialog / `task_row.py` / `COMMENTED` status / config keys (PR-3). No behaviour change in PR-1 — `add_comment` is never CALLED yet.
