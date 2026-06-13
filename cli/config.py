"""Configuration resolution for the CLI: flag > env > config.json.

The agent host (e.g. a headless VPS running Hermes Agent) may have no
``config.json`` at all, so every secret / setting must be overridable via a
command-line flag or an ``VOXNOTE_*`` environment variable. This
module centralises that precedence so every subcommand resolves settings the
same way.
"""
from __future__ import annotations

import os

ENV_PREFIX = "VOXNOTE_"

# VOXNOTE_<SUFFIX> env var → config.json key. Lets backend_from_name()
# (which reads secrets out of the config dict) work on a host with no
# config.json by sourcing keys from the environment.
_ENV_CONFIG_KEYS = {
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "LINEAR_API_KEY": "linear_api_key",
    "GLIDE_API_KEY": "glide_api_key",
    "TRELLO_API_KEY": "trello_api_key",
    "TRELLO_TOKEN": "trello_token",
}


def base_config() -> dict:
    """Return ``config.json`` contents, or ``{}`` when absent/unreadable.

    Never raises: a missing or malformed config.json must not stop an agent
    that passes everything via flags/env. ``utils.load_config`` already returns
    ``{}`` for a missing file; we additionally swallow a malformed file
    (bad JSON / unreadable) because the CLI can run fully from flags + env.
    """
    from utils import load_config
    try:
        return load_config()
    except (OSError, ValueError):
        # ValueError covers json.JSONDecodeError — fall back to flags/env.
        return {}


def merged_config() -> dict:
    """``base_config()`` overlaid with ``VOXNOTE_*`` env secrets.

    The result is the dict handed to ``tasks.backends.backend_from_name`` and
    the OpenRouter key lookup — so backend / LLM credentials can come from the
    environment even when no config.json exists.
    """
    cfg = dict(base_config())
    for env_suffix, cfg_key in _ENV_CONFIG_KEYS.items():
        val = os.environ.get(ENV_PREFIX + env_suffix)
        if val:
            cfg[cfg_key] = val
    return cfg


def resolve(flag, env_suffix, config_value, default=None):
    """Resolve a single setting by precedence: flag > env > config > default.

    ``flag`` and ``config_value`` count as "unset" when None or empty string,
    so an explicit ``--flag ''`` does not shadow a real env/config value.
    ``env_suffix`` is appended to ``VOXNOTE_`` (e.g. "PROVIDER" →
    ``VOXNOTE_PROVIDER``).
    """
    if flag:
        return flag
    env = os.environ.get(ENV_PREFIX + env_suffix)
    if env:
        return env
    if config_value:
        return config_value
    return default
