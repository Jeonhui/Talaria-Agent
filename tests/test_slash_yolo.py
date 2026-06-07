"""Coverage for the extracted ``/yolo`` handler (gateway/slash_yolo.py).

The toggle interacts with the per-session YOLO state in
``tools.approval``.  Stubbing that state keeps the test deterministic
and avoids touching the real session-state file on disk.
"""

import asyncio

from gateway.slash_yolo import handle_yolo


class _YoloState:
    """In-memory stand-in for the per-session YOLO state in
    ``tools.approval``."""

    def __init__(self):
        self.enabled: set[str] = set()


def _patch_approval(monkeypatch, state: _YoloState):
    import tools.approval as approval

    monkeypatch.setattr(approval, "is_session_yolo_enabled", lambda key: key in state.enabled)
    monkeypatch.setattr(approval, "enable_session_yolo", lambda key: state.enabled.add(key))
    monkeypatch.setattr(approval, "disable_session_yolo", lambda key: state.enabled.discard(key))


def test_yolo_off_to_on_returns_warning_and_enables(monkeypatch):
    state = _YoloState()
    _patch_approval(monkeypatch, state)

    msg = asyncio.run(handle_yolo("agent:main:telegram:dm:100"))

    assert "agent:main:telegram:dm:100" in state.enabled
    assert "**ON**" in msg
    assert "auto-approved" in msg


def test_yolo_on_to_off_returns_safety_message_and_disables(monkeypatch):
    state = _YoloState()
    state.enabled.add("agent:main:telegram:dm:100")
    _patch_approval(monkeypatch, state)

    msg = asyncio.run(handle_yolo("agent:main:telegram:dm:100"))

    assert "agent:main:telegram:dm:100" not in state.enabled
    assert "**OFF**" in msg
    assert "require approval" in msg


def test_yolo_is_scoped_to_session_key(monkeypatch):
    state = _YoloState()
    _patch_approval(monkeypatch, state)

    asyncio.run(handle_yolo("agent:main:telegram:dm:100"))
    asyncio.run(handle_yolo("agent:main:slack:channel:C123"))

    assert state.enabled == {
        "agent:main:telegram:dm:100",
        "agent:main:slack:channel:C123",
    }


def test_yolo_round_trip_is_idempotent_after_two_toggles(monkeypatch):
    """on → off → on → off lands back at the original state."""
    state = _YoloState()
    _patch_approval(monkeypatch, state)
    key = "agent:main:discord:channel:42"

    asyncio.run(handle_yolo(key))
    asyncio.run(handle_yolo(key))

    assert key not in state.enabled  # back to off
