"""Backend abstraction for the meeting-tasks pipeline.

A `TaskBackend` is the source-of-truth for «куда отправлять извлечённые
задачи» — it abstracts over Linear (GraphQL, teams, label_ids), Glide
(REST, boards, columns), and Trello (REST, lists, cards). The dialog and
sender depend only on the Protocol; concrete adapters live in linear.py,
glide.py, and trello.py.

Public entry points:
    Container, CreatedIssue        — value types
    TaskBackend                    — Protocol every adapter must satisfy
    LinearBackend, GlideBackend, TrelloBackend    — concrete adapters
    backend_from_name(name, cfg)   — factory keyed by config flags

Phase 6.4.1 (initial wiring): Linear has full feature parity with prior
behaviour; Glide is title+description+priority only (assignee/labels are
manual in editor — no LLM grounding).
"""
from tasks.backends.base import Container, CreatedIssue, TaskBackend
from tasks.backends.glide import GlideBackend
from tasks.backends.linear import LinearBackend
from tasks.backends.trello import TrelloBackend


def backend_from_name(name: str, config: dict) -> TaskBackend:
    """Construct a backend instance from its name + config dict.

    Reads the API key out of config[<backend>_api_key]. Raises ValueError
    on unknown name.
    """
    if name == "linear":
        from tasks.linear_client import LinearClient
        client = LinearClient(config.get("linear_api_key", ""))
        return LinearBackend(client)
    if name == "glide":
        from tasks.glide_client import GlideClient
        client = GlideClient(config.get("glide_api_key", ""))
        return GlideBackend(client)
    if name == "trello":
        from tasks.trello_client import TrelloClient
        client = TrelloClient(
            config.get("trello_api_key", ""),
            config.get("trello_token", ""),
        )
        return TrelloBackend(client)
    raise ValueError(f"Unknown backend: {name!r}")


__all__ = [
    "Container", "CreatedIssue", "TaskBackend",
    "LinearBackend", "GlideBackend", "TrelloBackend",
    "backend_from_name",
]
