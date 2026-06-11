"""Provider modules must not re-grow transport plumbing (audit Variant 3).

After the _common lift, ALL HTTP calls, sleep/deadline machinery and the
requests-error idiom live in providers/_common.py. If any forbidden
substring reappears in a provider module, the dedup is regressing — move
the new code into _common instead. Same spirit as test_widget_tree_split.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROVIDER_DIR = Path(__file__).resolve().parent.parent / "providers"
_MODULES = ["assemblyai.py", "deepgram.py", "gladia.py", "speechmatics.py"]
_FORBIDDEN = [
    "requests.get(",
    "requests.post(",
    "requests.delete(",
    "requests.request(",
    "except requests.RequestException",
    "time.sleep(",
    "time.monotonic(",
]


@pytest.mark.parametrize("module", _MODULES)
def test_no_transport_plumbing_in_provider_modules(module):
    src = (_PROVIDER_DIR / module).read_text(encoding="utf-8")
    hits = [s for s in _FORBIDDEN if s in src]
    assert not hits, (
        f"{module} re-grew transport plumbing {hits} — "
        f"it belongs in providers/_common.py"
    )
