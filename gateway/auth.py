"""User authorization for inbound gateway messages.

Pure functions extracted from ``gateway/run.py`` so the authorization
policy can be reasoned about (and tested) independently of the
``GatewayRunner`` class. The runner now delegates to these helpers via
thin wrappers.

The two entry points are:

- :func:`is_user_authorized` — gate inbound messages on env-var allowlists,
  pairing-store approvals, and per-platform allow-all flags.
- :func:`get_unauthorized_dm_behavior` — decide whether to send a pairing
  code or silently ignore an unauthorized DM, based on operator config.

Built-in platforms use a fixed env-var schema (``<PLAT>_ALLOWED_USERS``,
``<PLAT>_ALLOW_ALL_USERS``).  Plugin platforms register their env-var
names with the platform registry, which is consulted on the fly.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource

logger = logging.getLogger(__name__)


_BUILTIN_PLATFORM_ENV_MAP: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_ALLOWED_USERS",
    Platform.DISCORD: "DISCORD_ALLOWED_USERS",
    Platform.SLACK: "SLACK_ALLOWED_USERS",
}

_BUILTIN_PLATFORM_GROUP_USER_ENV_MAP: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_GROUP_ALLOWED_USERS",
}

_BUILTIN_PLATFORM_GROUP_CHAT_ENV_MAP: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_GROUP_ALLOWED_CHATS",
}

_BUILTIN_PLATFORM_ALLOW_ALL_MAP: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_ALLOW_ALL_USERS",
    Platform.DISCORD: "DISCORD_ALLOW_ALL_USERS",
    Platform.SLACK: "SLACK_ALLOW_ALL_USERS",
}

# Process-wide one-shot warning latch for the legacy
# TELEGRAM_GROUP_ALLOWED_USERS chat-ID shim (see issue #15027).
_legacy_telegram_chat_id_warned = False


def _maybe_load_plugin_env_names(platform: Platform) -> tuple[Optional[str], Optional[str]]:
    """Look up plugin-registered allowlist / allow-all env var names.

    Returns ``(allowed_users_env, allow_all_env)``, either of which may be
    ``None`` when the platform is not plugin-registered or the entry omits
    that field.
    """
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(platform.value)
        if entry:
            return (
                getattr(entry, "allowed_users_env", None),
                getattr(entry, "allow_all_env", None),
            )
    except Exception:
        pass
    return None, None


def is_user_authorized(source: SessionSource, pairing_store) -> bool:
    """Check whether the sender of ``source`` is allowed to use the gateway.

    Decision order:

    1. Webhook events are pre-authenticated via HMAC in the adapter.
    2. Per-platform allow-all flag (``<PLAT>_ALLOW_ALL_USERS``).
    3. Discord ``DISCORD_ALLOW_BOTS`` / ``DISCORD_ALLOWED_ROLES`` shortcuts.
    4. Pairing store — explicit per-user approvals.
    5. Per-platform / per-group / global allowlists.
    6. ``GATEWAY_ALLOW_ALL_USERS`` as the open-gateway fallback.
    7. Default: deny.

    ``pairing_store`` is the runner's ``PairingStore`` instance.  It is
    passed in instead of re-imported so tests can inject a fake.
    """
    global _legacy_telegram_chat_id_warned

    # Webhook events are authenticated via HMAC signature validation in
    # the adapter itself — no user allowlist applies.
    if source.platform == Platform.WEBHOOK:
        return True

    user_id = source.user_id
    if not user_id:
        return False

    platform_env_map = dict(_BUILTIN_PLATFORM_ENV_MAP)
    platform_group_user_env_map = dict(_BUILTIN_PLATFORM_GROUP_USER_ENV_MAP)
    platform_group_chat_env_map = dict(_BUILTIN_PLATFORM_GROUP_CHAT_ENV_MAP)
    platform_allow_all_map = dict(_BUILTIN_PLATFORM_ALLOW_ALL_MAP)

    if source.platform not in platform_env_map:
        allowed_env, allow_all_env = _maybe_load_plugin_env_names(source.platform)
        if allowed_env:
            platform_env_map[source.platform] = allowed_env
        if allow_all_env:
            platform_allow_all_map[source.platform] = allow_all_env

    # Per-platform allow-all flag (e.g., DISCORD_ALLOW_ALL_USERS=true).
    platform_allow_all_var = platform_allow_all_map.get(source.platform, "")
    if platform_allow_all_var and os.getenv(platform_allow_all_var, "").lower() in ("true", "1", "yes"):
        return True

    # Discord bot senders that passed the DISCORD_ALLOW_BOTS platform
    # filter are already authorized at the platform level (#4466).
    if source.platform == Platform.DISCORD and getattr(source, "is_bot", False):
        allow_bots = os.getenv("DISCORD_ALLOW_BOTS", "none").lower().strip()
        if allow_bots in ("mentions", "all"):
            return True

    # Discord role-based access: adapter's on_message pre-filter already
    # verified role membership (#7871).
    if source.platform == Platform.DISCORD and os.getenv("DISCORD_ALLOWED_ROLES", "").strip():
        return True

    # Pairing store — checked unconditionally so explicit user approvals
    # bypass the allowlists.
    platform_name = source.platform.value if source.platform else ""
    if pairing_store.is_approved(platform_name, user_id):
        return True

    # Platform-specific and global allowlists.
    platform_allowlist = os.getenv(platform_env_map.get(source.platform, ""), "").strip()
    group_user_allowlist = ""
    group_chat_allowlist = ""
    if source.chat_type in {"group", "forum"}:
        group_user_allowlist = os.getenv(platform_group_user_env_map.get(source.platform, ""), "").strip()
        group_chat_allowlist = os.getenv(platform_group_chat_env_map.get(source.platform, ""), "").strip()
    global_allowlist = os.getenv("GATEWAY_ALLOWED_USERS", "").strip()

    if not platform_allowlist and not group_user_allowlist and not group_chat_allowlist and not global_allowlist:
        # No allowlists configured -- check global allow-all flag.
        return os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in ("true", "1", "yes")

    # Telegram can authorize group traffic by chat ID
    # (distinct from TELEGRAM_GROUP_ALLOWED_USERS, which gates sender IDs).
    if group_chat_allowlist and source.chat_type in {"group", "forum"} and source.chat_id:
        allowed_group_ids = {
            chat_id.strip() for chat_id in group_chat_allowlist.split(",") if chat_id.strip()
        }
        if "*" in allowed_group_ids or source.chat_id in allowed_group_ids:
            return True

    # Backward-compat shim for #15027: prior to PR #17686,
    # TELEGRAM_GROUP_ALLOWED_USERS was (mis)used as a chat-ID allowlist.
    # Values starting with "-" are Telegram chat IDs, not user IDs — honor
    # them as chat IDs and warn once.
    if (
        source.platform == Platform.TELEGRAM
        and group_user_allowlist
        and source.chat_type in {"group", "forum"}
        and source.chat_id
    ):
        legacy_chat_ids = {
            v.strip()
            for v in group_user_allowlist.split(",")
            if v.strip().startswith("-")
        }
        if legacy_chat_ids:
            if not _legacy_telegram_chat_id_warned:
                logger.warning(
                    "TELEGRAM_GROUP_ALLOWED_USERS contains chat-ID-shaped values "
                    "(%s). Treating them as chat IDs for backward compatibility. "
                    "Move chat IDs to TELEGRAM_GROUP_ALLOWED_CHATS — the _USERS var "
                    "is now for sender user IDs.",
                    ",".join(sorted(legacy_chat_ids)),
                )
                _legacy_telegram_chat_id_warned = True
            if source.chat_id in legacy_chat_ids:
                return True

    # Aggregate allowlist membership check.  In group/forum chats,
    # TELEGRAM_GROUP_ALLOWED_USERS is the scoped allowlist and should not
    # imply DM access; TELEGRAM_ALLOWED_USERS remains the platform-wide
    # allowlist and still works everywhere for backward compatibility.
    allowed_ids: set[str] = set()
    if platform_allowlist:
        allowed_ids.update(uid.strip() for uid in platform_allowlist.split(",") if uid.strip())
    if group_user_allowlist:
        allowed_ids.update(uid.strip() for uid in group_user_allowlist.split(",") if uid.strip())
    if global_allowlist:
        allowed_ids.update(uid.strip() for uid in global_allowlist.split(",") if uid.strip())

    if "*" in allowed_ids:
        return True

    check_ids = {user_id}
    if "@" in user_id:
        check_ids.add(user_id.split("@")[0])

    return bool(check_ids & allowed_ids)


def get_unauthorized_dm_behavior(platform: Optional[Platform], config: Optional[GatewayConfig]) -> str:
    """Decide how to handle an unauthorized DM.

    Resolution order:

    1. Explicit per-platform ``unauthorized_dm_behavior`` in config.
    2. Explicit global ``unauthorized_dm_behavior`` in config.
    3. Any allowlist configured for the platform → ``"ignore"``
       (#9337 — silently drop instead of leaking pairing codes).
    4. ``GATEWAY_ALLOWED_USERS`` configured → ``"ignore"``.
    5. Default: ``"pair"`` (open-gateway).
    """
    if config is not None and hasattr(config, "get_unauthorized_dm_behavior") and platform is not None:
        platform_cfg = config.platforms.get(platform) if hasattr(config, "platforms") else None
        if platform_cfg and "unauthorized_dm_behavior" in getattr(platform_cfg, "extra", {}):
            return config.get_unauthorized_dm_behavior(platform)

    if config is not None and hasattr(config, "unauthorized_dm_behavior"):
        if config.unauthorized_dm_behavior != "pair":
            return config.unauthorized_dm_behavior

    if platform is not None:
        if os.getenv(_BUILTIN_PLATFORM_ENV_MAP.get(platform, ""), "").strip():
            return "ignore"
        group_user_env = _BUILTIN_PLATFORM_GROUP_USER_ENV_MAP.get(platform, "")
        group_chat_env = _BUILTIN_PLATFORM_GROUP_CHAT_ENV_MAP.get(platform, "")
        if (group_user_env and os.getenv(group_user_env, "").strip()) or (
            group_chat_env and os.getenv(group_chat_env, "").strip()
        ):
            return "ignore"

    if os.getenv("GATEWAY_ALLOWED_USERS", "").strip():
        return "ignore"

    return "pair"
