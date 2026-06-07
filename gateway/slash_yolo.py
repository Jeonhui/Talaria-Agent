"""Self-contained ``/yolo`` slash-command handler.

``/yolo`` flips the per-session dangerous-command approval bypass.  It
touches no ``GatewayRunner`` state beyond the session key, so the
runner method now collapses to a delegator that resolves the session
key and forwards.

Splitting this out (a) keeps the runner thinner, and (b) lets the
toggle logic be unit-tested with a stub approval module instead of a
partially-constructed ``GatewayRunner``.
"""

from __future__ import annotations


async def handle_yolo(session_key: str) -> str:
    """Toggle YOLO mode for ``session_key`` and return the user-facing
    confirmation message.

    YOLO mode auto-approves every dangerous terminal command for the
    current session.  Disabled by default; flipping it on is meant as
    an in-conversation escape hatch for users who are sure of what
    they're doing.
    """
    from tools.approval import (
        disable_session_yolo,
        enable_session_yolo,
        is_session_yolo_enabled,
    )

    if is_session_yolo_enabled(session_key):
        disable_session_yolo(session_key)
        return (
            "⚠️ YOLO mode **OFF** for this session — "
            "dangerous commands will require approval."
        )

    enable_session_yolo(session_key)
    return (
        "⚡ YOLO mode **ON** for this session — "
        "all commands auto-approved. Use with caution."
    )
