"""Runtime bridge to the active integration module.

A thin, import-safe facade so the gateway and agent can call the active
:class:`~agent.integration_module.IntegrationModule` at a handful of wiring
points with one-liners. Every function is null-safe: when no module is
configured (or one errors), the helpers degrade to "no module" behavior so
the agent runs completely unchanged.

Wiring points (all optional, each a single call):
  - auth      : is_authorized(source)        -> gateway/auth.py
  - logging   : log_message / log_response   -> gateway/run.py
  - MCP       : mcp_server_config()          -> MCP registration
  - tools     : available_tools(source)      -> tool gating
  - context   : context_files(source)        -> prompt builder
  - skills    : skills(source)               -> auto-skill loading

Identity is resolved through a short-TTL cache so a single inbound message
that touches several wiring points only hits the backend once.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from agent.integration_module import IntegrationModule, UserInfo
    from gateway.session import SessionSource

logger = logging.getLogger(__name__)

# Cache the active module instance (loading scans plugins + reads config).
_module_lock = threading.Lock()
_module_loaded = False
_module: Optional["IntegrationModule"] = None

# Short-TTL identity cache, keyed by (platform, user_id).
_USER_TTL_SECONDS = 60.0
_user_cache_lock = threading.Lock()
_user_cache: dict[tuple[str, str], tuple[float, "UserInfo"]] = {}


# ---------------------------------------------------------------------------
# Module access
# ---------------------------------------------------------------------------

def active_module() -> Optional["IntegrationModule"]:
    """Return the active integration module, or None. Cached + null-safe."""
    global _module_loaded, _module
    with _module_lock:
        if _module_loaded:
            return _module
        _module_loaded = True
        try:
            from integrations import get_active_integration_module
            module = get_active_integration_module()
            if module and module.is_available():
                _module = module
            else:
                if module:
                    logger.info(
                        "Integration module '%s' configured but not available "
                        "(missing config/creds) — running without it.",
                        getattr(module, "name", "?"),
                    )
                _module = None
        except Exception as exc:
            logger.warning("Failed to load integration module: %s", exc)
            _module = None
        return _module


def reset_cache() -> None:
    """Forget the cached module + identities (call after config changes)."""
    global _module_loaded, _module
    with _module_lock:
        _module_loaded = False
        _module = None
    with _user_cache_lock:
        _user_cache.clear()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def resolve_user(source: "SessionSource") -> Optional["UserInfo"]:
    """Resolve a sender to a UserInfo via the active module, or None.

    Returns None when no module is active or resolution fails. Results are
    cached per (platform, user_id) for a short TTL.
    """
    module = active_module()
    if module is None:
        return None

    platform = source.platform.value if getattr(source, "platform", None) else ""
    user_id = getattr(source, "user_id", "") or ""
    if not user_id:
        return None

    key = (platform, user_id)
    now = time.monotonic()
    with _user_cache_lock:
        hit = _user_cache.get(key)
        if hit and (now - hit[0]) < _USER_TTL_SECONDS:
            return hit[1]

    try:
        user = module.resolve_user(
            mcp_key=module.mcp_key(),
            platform=platform,
            user_id=user_id,
            user_name=getattr(source, "user_name", "") or "",
        )
    except Exception as exc:
        logger.warning("Integration resolve_user failed for %s/%s: %s",
                       platform, user_id, exc)
        return None

    # Only cache authorized users. Denied verdicts are re-checked every
    # message so a server-side ban (or revocation) takes effect immediately
    # instead of lingering for the cache TTL.
    if user.authorized:
        with _user_cache_lock:
            _user_cache[key] = (now, user)
    return user


def is_authorized(source: "SessionSource") -> Optional[bool]:
    """Authorization verdict from the active module.

    Returns:
      - None  when no module is active → caller falls back to env allowlist
      - True/False from the resolved user's ``authorized`` flag
    """
    module = active_module()
    if module is None:
        return None
    user = resolve_user(source)
    if user is None:
        # Module active but couldn't resolve → deny (fail closed).
        return False
    return bool(user.authorized)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_message(source: "SessionSource", text: str, **ctx) -> None:
    """Record an inbound user message via the active module. Null-safe."""
    module = active_module()
    if module is None:
        return
    try:
        module.log_message(resolve_user(source), text, **ctx)
    except Exception as exc:
        logger.debug("Integration log_message failed: %s", exc)


def log_response(source: "SessionSource", text: str, **ctx) -> None:
    """Record an outbound agent response via the active module. Null-safe."""
    module = active_module()
    if module is None:
        return
    try:
        module.log_response(resolve_user(source), text, **ctx)
    except Exception as exc:
        logger.debug("Integration log_response failed: %s", exc)


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

def mcp_server_config() -> dict:
    """Return ``{name: server_config}`` for the module's MCP server, or {}.

    The returned entry plugs straight into
    ``tools.mcp_tool.register_mcp_servers`` — it inherits that module's
    automatic reconnect/backoff machinery.
    """
    module = active_module()
    if module is None:
        return {}
    try:
        url = module.mcp_url()
        key = module.mcp_key()
    except Exception as exc:
        logger.debug("Integration mcp_server_config failed: %s", exc)
        return {}
    if not url:
        return {}
    cfg: dict = {"url": url, "enabled": True}
    if key:
        cfg["headers"] = {"Authorization": f"Bearer {key}"}
    return {module.name: cfg}


# ---------------------------------------------------------------------------
# Tools / context / skills
# ---------------------------------------------------------------------------

def available_tools(source: "SessionSource") -> Optional[List[str]]:
    """MCP tool gate for this user.

    Returns:
      - ``None`` → no restriction (no module, or module returned None).
      - ``[]``   → zero MCP tools (user unresolved, or module said so) —
        fail-closed.
      - ``[...]``→ restrict to these tool names.
    """
    module = active_module()
    if module is None:
        return None
    user = resolve_user(source)
    if user is None:
        return []  # fail-closed: deny MCP tools when identity is unknown
    try:
        result = module.available_tools(user)
    except Exception as exc:
        logger.debug("Integration available_tools failed: %s", exc)
        return []  # fail-closed on error
    return None if result is None else list(result)


def context_files(source: "SessionSource") -> List[str]:
    """Per-user context file paths from the active module, or []."""
    module = active_module()
    if module is None:
        return []
    user = resolve_user(source)
    if user is None:
        return []
    try:
        return list(module.context_files(user))
    except Exception as exc:
        logger.debug("Integration context_files failed: %s", exc)
        return []


def skills(source: "SessionSource") -> List[str]:
    """Per-user auto-load skill names from the active module, or []."""
    module = active_module()
    if module is None:
        return []
    user = resolve_user(source)
    if user is None:
        return []
    try:
        return list(module.skills(user))
    except Exception as exc:
        logger.debug("Integration skills failed: %s", exc)
        return []
