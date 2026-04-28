"""tasks/ — meeting-tasks pipeline.

Submodules:
- schema: Task dataclass and enums.
- openrouter_client: thin REST wrapper for OpenRouter chat completions.
- linear_client: thin GraphQL wrapper for Linear (viewer/teams/issues).
- extractor: (Phase 6.1) orchestrator that turns a transcript into Task[].
- persistence: (Phase 6.1) save/load tasks_raw.json and tasks.json.
"""
