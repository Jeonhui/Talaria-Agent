"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def talaria_xai_user_agent() -> str:
    """Return a stable Talaria-specific User-Agent for xAI HTTP calls."""
    try:
        from talaria_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"Talaria-Agent/{__version__}"
