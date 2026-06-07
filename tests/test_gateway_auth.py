"""Gateway user-authorization policy (gateway/auth.py).

These tests lock in the behavior of ``is_user_authorized`` and
``get_unauthorized_dm_behavior`` so the recent extraction out of
``gateway/run.py`` (PR #5) stays semantically equivalent to the old
``GatewayRunner._is_user_authorized`` / ``_get_unauthorized_dm_behavior``.

The functions are intentionally driven by environment variables and an
external ``pairing_store``, so the tests stub the latter with a tiny
in-memory fake and use ``monkeypatch`` for the env.
"""

import pytest

from gateway.auth import (
    get_unauthorized_dm_behavior,
    is_user_authorized,
)
from gateway.config import Platform
from gateway.session import SessionSource


class FakePairingStore:
    """Minimal stand-in for ``gateway.pairing.PairingStore``."""

    def __init__(self, approved=None):
        self._approved = set(approved or [])

    def is_approved(self, platform: str, user_id: str) -> bool:
        return (platform, user_id) in self._approved


def _source(
    *,
    platform: Platform = Platform.TELEGRAM,
    user_id: str = "100",
    chat_type: str = "dm",
    chat_id: str = "100",
    is_bot: bool = False,
) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        is_bot=is_bot,
    )


# ── is_user_authorized ─────────────────────────────────────────────────────


def test_webhook_is_always_authorized(monkeypatch):
    """Webhook events are HMAC-validated in the adapter; no allowlist applies."""
    src = _source(platform=Platform.WEBHOOK, user_id="")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_missing_user_id_is_denied(monkeypatch):
    src = _source(user_id="")
    assert is_user_authorized(src, FakePairingStore()) is False


def test_no_allowlist_no_allow_all_denies(monkeypatch):
    src = _source(user_id="100")
    assert is_user_authorized(src, FakePairingStore()) is False


def test_gateway_allow_all_users_opens_gateway(monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    src = _source(user_id="100")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_per_platform_allow_all_opens_only_that_platform(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", "true")
    tg = _source(platform=Platform.TELEGRAM, user_id="100")
    sl = _source(platform=Platform.SLACK, user_id="U100")
    assert is_user_authorized(tg, FakePairingStore()) is True
    assert is_user_authorized(sl, FakePairingStore()) is False


def test_pairing_store_approval_bypasses_allowlist(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")  # not us
    src = _source(user_id="100")
    store = FakePairingStore(approved=[("telegram", "100")])
    assert is_user_authorized(src, store) is True


def test_platform_allowlist_grants_matching_user(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "100,200")
    assert is_user_authorized(_source(user_id="100"), FakePairingStore()) is True
    assert is_user_authorized(_source(user_id="200"), FakePairingStore()) is True
    assert is_user_authorized(_source(user_id="300"), FakePairingStore()) is False


def test_platform_allowlist_wildcard_grants_everyone(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    assert is_user_authorized(_source(user_id="100"), FakePairingStore()) is True
    assert is_user_authorized(_source(user_id="anything"), FakePairingStore()) is True


def test_global_allowlist_grants_across_platforms(monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "100")
    tg = _source(platform=Platform.TELEGRAM, user_id="100")
    sl = _source(platform=Platform.SLACK, user_id="100")
    assert is_user_authorized(tg, FakePairingStore()) is True
    assert is_user_authorized(sl, FakePairingStore()) is True


def test_at_handle_matches_local_part(monkeypatch):
    """User ids of the form 'user@domain' should also match the 'user' prefix."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "alice")
    src = _source(user_id="alice@example.com")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_telegram_group_allowed_chats_authorizes_group_traffic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "-100200300")
    src = _source(chat_type="group", chat_id="-100200300", user_id="42")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_telegram_group_chat_wildcard(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "*")
    src = _source(chat_type="group", chat_id="-100200300", user_id="42")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_telegram_group_allowed_users_legacy_chat_id_shim(monkeypatch):
    """Legacy values starting with '-' in TELEGRAM_GROUP_ALLOWED_USERS are honored as chat IDs (#15027)."""
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "-100200300")
    src = _source(chat_type="group", chat_id="-100200300", user_id="42")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_discord_allowed_roles_shortcut_grants_access(monkeypatch):
    """If DISCORD_ALLOWED_ROLES is set, the adapter pre-filter already verified roles."""
    monkeypatch.setenv("DISCORD_ALLOWED_ROLES", "admin")
    src = _source(platform=Platform.DISCORD, user_id="snowflake-1")
    assert is_user_authorized(src, FakePairingStore()) is True


def test_discord_bot_with_allow_bots_grants_access(monkeypatch):
    """Bots flagged by DISCORD_ALLOW_BOTS=mentions|all skip the user allowlist (#4466)."""
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "all")
    src = _source(platform=Platform.DISCORD, user_id="bot-1", is_bot=True)
    assert is_user_authorized(src, FakePairingStore()) is True


def test_discord_bot_without_allow_bots_uses_normal_check(monkeypatch):
    src = _source(platform=Platform.DISCORD, user_id="bot-1", is_bot=True)
    assert is_user_authorized(src, FakePairingStore()) is False


# ── get_unauthorized_dm_behavior ───────────────────────────────────────────


def test_default_behavior_is_pair(monkeypatch):
    """No config, no allowlist → default open-gateway pair behavior."""
    assert get_unauthorized_dm_behavior(Platform.TELEGRAM, config=None) == "pair"


def test_platform_allowlist_implies_ignore(monkeypatch):
    """Operator deliberately restricted access; don't leak pairing codes (#9337)."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "100")
    assert get_unauthorized_dm_behavior(Platform.TELEGRAM, config=None) == "ignore"


def test_gateway_allowlist_implies_ignore(monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "100")
    assert get_unauthorized_dm_behavior(Platform.SLACK, config=None) == "ignore"


def test_platform_group_allowlist_implies_ignore(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "100")
    assert get_unauthorized_dm_behavior(Platform.TELEGRAM, config=None) == "ignore"


def test_explicit_global_override_wins(monkeypatch):
    """An explicit config.unauthorized_dm_behavior overrides the allowlist heuristic."""

    class _Cfg:
        unauthorized_dm_behavior = "ignore"  # operator wants ignore even without allowlists

    assert get_unauthorized_dm_behavior(Platform.TELEGRAM, config=_Cfg()) == "ignore"


def test_explicit_per_platform_override_wins(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "100")  # would normally trigger ignore

    class _Plat:
        extra = {"unauthorized_dm_behavior": "pair"}

    class _Cfg:
        unauthorized_dm_behavior = "pair"
        platforms = {Platform.TELEGRAM: _Plat()}

        def get_unauthorized_dm_behavior(self, platform):
            return "pair"

    assert get_unauthorized_dm_behavior(Platform.TELEGRAM, config=_Cfg()) == "pair"


@pytest.mark.parametrize("env_val", ["TRUE", "1", "yes", "true"])
def test_allow_all_accepts_truthy_strings(monkeypatch, env_val):
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", env_val)
    assert is_user_authorized(_source(user_id="100"), FakePairingStore()) is True


@pytest.mark.parametrize("env_val", ["false", "no", "0", "FALSE", "", "maybe"])
def test_allow_all_rejects_falsy_strings(monkeypatch, env_val):
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", env_val)
    assert is_user_authorized(_source(user_id="100"), FakePairingStore()) is False
