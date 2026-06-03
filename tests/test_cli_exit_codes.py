"""exit_code_for maps each pipeline exception class to its process exit code.

These imports (providers / tasks / transcriber) are CI-safe: none pull
sounddevice/PortAudio at import (transcriber loads cloud_chunker — the only
soundfile user — lazily). Mirrors the existing test_providers_* imports.
"""
from __future__ import annotations

from cli.app import (
    EXIT_BACKEND,
    EXIT_CANCELLED,
    EXIT_CONFIG,
    EXIT_GENERIC,
    EXIT_LLM,
    EXIT_TRANSCRIBE,
    exit_code_for,
)
from providers import ProviderError
from tasks.extractor import ExtractionError
from tasks.glide_client import GlideError
from tasks.linear_client import LinearError
from tasks.openrouter_client import OpenRouterError
from tasks.protocol_generator import ProtocolGenerationError
from tasks.trello_client import TrelloError
from transcriber import TranscriptionCancelled


def test_cancelled_is_130():
    assert exit_code_for(TranscriptionCancelled()) == EXIT_CANCELLED


def test_valueerror_is_config_3():
    # _cmd_* handlers raise ValueError up-front for an empty key / bad config.
    assert exit_code_for(ValueError("Нет API-ключа")) == EXIT_CONFIG


def test_backend_errors_are_6():
    assert exit_code_for(LinearError("x")) == EXIT_BACKEND
    assert exit_code_for(GlideError("x")) == EXIT_BACKEND
    assert exit_code_for(TrelloError("x")) == EXIT_BACKEND


def test_llm_errors_are_5():
    assert exit_code_for(OpenRouterError("x")) == EXIT_LLM
    assert exit_code_for(ExtractionError("x")) == EXIT_LLM
    assert exit_code_for(ProtocolGenerationError("x")) == EXIT_LLM


def test_provider_and_runtime_are_transcribe_4():
    # ProviderError subclasses RuntimeError; Transcriber also re-wraps provider
    # HTTP errors as RuntimeError — both are runtime transcription failures.
    assert exit_code_for(ProviderError("http 500")) == EXIT_TRANSCRIBE
    assert exit_code_for(RuntimeError("rewrapped provider error")) == EXIT_TRANSCRIBE


def test_unknown_exception_is_generic_1():
    assert exit_code_for(KeyError("x")) == EXIT_GENERIC
    assert exit_code_for(TypeError("x")) == EXIT_GENERIC
