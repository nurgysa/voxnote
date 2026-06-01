# tests/test_dedup_llm_descriptions.py
import json

from tasks.dedup import SentTask, disambiguate_via_llm
from tasks.schema import Priority, Task, TaskStatus


class _FakeOR:
    def __init__(self):
        self.last_messages = None

    def complete(self, *, model, messages, json_mode):
        self.last_messages = messages
        return {"content": json.dumps({"match_id": None})}


def _task(title, desc=""):
    return Task(
        local_id="l1", title=title, description=desc, priority=Priority.MEDIUM,
        status=TaskStatus.PENDING,
    )


def test_llm_prompt_includes_descriptions():
    cand = SentTask(
        title="Изучить систему СУП", backend="linear", container_id="t",
        ref="r", identifier="NUR-37", url="", meeting_name="", meeting_date="",
        description="Погрузиться в систему СУП для интеграции",
    )
    orc = _FakeOR()
    disambiguate_via_llm(
        _task("Изучить СУП", "Разобрать интерфейс СУП"), [cand], orc, "model-x",
    )
    user_msg = orc.last_messages[1]["content"]
    assert "Разобрать интерфейс СУП" in user_msg          # new task desc
    assert "Погрузиться в систему СУП для интеграции" in user_msg  # candidate desc
