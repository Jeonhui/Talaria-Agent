"""Session key builder (gateway/session.py::build_session_key).

This is the single source of truth for how messages are routed to
session entries — the gateway, the cron delivery path, and every CLI
diagnostic that prints session IDs depends on it producing the same
string for the same logical conversation.  Locking the format down here
makes the implicit contract explicit.
"""

from gateway.config import Platform
from gateway.session import SessionSource, build_session_key


def _src(**kwargs) -> SessionSource:
    defaults = {
        "platform": Platform.TELEGRAM,
        "chat_id": "",
        "chat_type": "dm",
    }
    defaults.update(kwargs)
    return SessionSource(**defaults)


# ── DM keys ────────────────────────────────────────────────────────────────


def test_dm_with_chat_id():
    key = build_session_key(_src(chat_id="100", chat_type="dm"))
    assert key == "agent:main:telegram:dm:100"


def test_dm_with_chat_id_and_thread():
    key = build_session_key(
        _src(chat_id="100", chat_type="dm", thread_id="T1")
    )
    assert key == "agent:main:telegram:dm:100:T1"


def test_dm_thread_only_fallback():
    key = build_session_key(_src(chat_type="dm", thread_id="T1"))
    assert key == "agent:main:telegram:dm:T1"


def test_dm_no_identifiers_shared_session():
    key = build_session_key(_src(chat_type="dm"))
    assert key == "agent:main:telegram:dm"


# ── Group / channel keys ───────────────────────────────────────────────────


def test_group_with_chat_id_and_user_isolation():
    key = build_session_key(
        _src(chat_type="group", chat_id="-100", user_id="42"),
        group_sessions_per_user=True,
    )
    assert key == "agent:main:telegram:group:-100:42"


def test_group_user_isolation_disabled_yields_shared_session():
    key = build_session_key(
        _src(chat_type="group", chat_id="-100", user_id="42"),
        group_sessions_per_user=False,
    )
    assert key == "agent:main:telegram:group:-100"


def test_channel_keys_keep_platform_prefix():
    key = build_session_key(
        _src(platform=Platform.SLACK, chat_type="channel", chat_id="C123", user_id="U1"),
        group_sessions_per_user=True,
    )
    assert key == "agent:main:slack:channel:C123:U1"


def test_user_id_alt_takes_precedence_over_user_id():
    """SessionSource.user_id_alt is a stable cross-rename ID (Signal UUID, Feishu
    union_id, etc.) — when present, it overrides the volatile user_id."""
    key = build_session_key(
        _src(
            chat_type="group",
            chat_id="-100",
            user_id="legacy-handle",
            user_id_alt="stable-uuid",
        ),
        group_sessions_per_user=True,
    )
    assert key == "agent:main:telegram:group:-100:stable-uuid"


# ── Thread sharing behaviour (the subtle bit) ──────────────────────────────


def test_thread_default_shares_session_across_users():
    """Default ``thread_sessions_per_user=False`` keeps every participant on
    the same thread session (Telegram forum topics, Discord threads, etc.)."""
    a = build_session_key(
        _src(chat_type="group", chat_id="-100", thread_id="T9", user_id="42"),
    )
    b = build_session_key(
        _src(chat_type="group", chat_id="-100", thread_id="T9", user_id="99"),
    )
    assert a == b == "agent:main:telegram:group:-100:T9"


def test_thread_per_user_isolation_when_enabled():
    a = build_session_key(
        _src(chat_type="group", chat_id="-100", thread_id="T9", user_id="42"),
        thread_sessions_per_user=True,
    )
    b = build_session_key(
        _src(chat_type="group", chat_id="-100", thread_id="T9", user_id="99"),
        thread_sessions_per_user=True,
    )
    assert a != b
    assert a == "agent:main:telegram:group:-100:T9:42"
    assert b == "agent:main:telegram:group:-100:T9:99"


def test_group_without_chat_id_uses_user_only():
    key = build_session_key(
        _src(chat_type="group", chat_id="", user_id="42"),
        group_sessions_per_user=True,
    )
    assert key == "agent:main:telegram:group:42"


def test_keys_are_deterministic_across_calls():
    """Same input → same output, every time."""
    s = _src(chat_id="100", chat_type="dm", thread_id="T1")
    assert build_session_key(s) == build_session_key(s)
