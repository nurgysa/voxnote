## Summary

<!-- What and why. One concern per PR. -->

## Test plan

- [ ] `pytest` green locally (baseline ≈ 748 tests)
- [ ] `python -m ruff check .` clean
- [ ] UI-touching change: state the manual smoke you ran (there is no automated GUI testing)
- [ ] Any new text-mode `open()` / `read_text()` / `write_text()` passes `encoding="utf-8"` (stock Windows defaults to cp1252)
