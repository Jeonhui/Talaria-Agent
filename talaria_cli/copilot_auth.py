"""Stub for the GitHub Copilot OAuth flow.

Talaria does not include the Copilot ACP provider. The public API is kept
as inert no-ops so existing call sites don't crash; nothing ever resolves
as authenticated.
"""

from __future__ import annotations

from typing import Optional


def validate_copilot_token(_token: str) -> tuple[bool, str]:
    return False, "Copilot is not supported in Talaria."


def resolve_copilot_token() -> tuple[str, str]:
    return "", ""


def exchange_copilot_token(_raw_token: str, *, timeout: float = 10.0) -> tuple[str, float]:
    return "", 0.0


def get_copilot_api_token(_raw_token: str) -> str:
    return ""


def copilot_request_headers(*_args, **_kwargs) -> dict:
    return {}


def copilot_device_code_login(*_args, **_kwargs) -> Optional[str]:
    raise RuntimeError("Copilot login is not supported in Talaria.")
