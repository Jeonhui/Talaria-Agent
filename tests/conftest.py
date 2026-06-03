"""Shared pytest fixtures.

Keeps tests deterministic and offline: blanks credential / backend env vars
before each test so config bridging and approval logic don't depend on the
developer's shell or real provider keys.
"""

import pytest

# Env vars that, if set in the dev shell, would make tests non-deterministic.
_BLANK = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "SUDO_PASSWORD",
    "TERMINAL_ENV",
    "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
    "TERMINAL_DOCKER_VOLUMES",
    "TERMINAL_SSH_STRICT_HOST_KEY",
    "TALARIA_REDACT_SECRETS",
    "TALARIA_IGNORE_USER_CONFIG",
    "TALARIA_INTERACTIVE",
    "TALARIA_GATEWAY_SESSION",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _BLANK:
        monkeypatch.delenv(key, raising=False)
    yield
