"""Abstract base class for a pluggable integration module.

An *integration module* bundles every external-service concern into one
swappable unit: identity resolution, MCP connection info, the set of MCP
tools a user may call, context files, skills, message logging, and the
setup data needed to configure all of the above.

One module is active at a time, selected via ``integration.module`` in
config.yaml. To swap backends (e.g. a different tenant/service), drop a
new directory under ``integrations/<name>/`` and point config at it —
everything (identity + MCP + logging + context + skills) changes together.

Integrations are a top-level core feature:

  1. Implementations ship in ``integrations/<name>/__init__.py``.
  2. Discovery / loading lives in ``integrations/__init__.py``.
  3. The active module is chosen by ``integration.module`` config.

Lifecycle / wiring (called from the gateway + agent):

  is_available()        — config + creds present, ready to use
  get_config_schema()   — fields for ``talaria integration setup``
  save_config()         — persist non-secret setup values
  resolve_user()        — authn/identity: credentials -> UserInfo
  available_tools()     — which MCP tools this user may call
  mcp_url() / mcp_key() — MCP endpoint + secret (read from env)
  context_files()       — extra context to inject for this user
  skills()              — skills to auto-load for this user
  log_message()         — record an inbound user message
  log_response()        — record an outbound agent response
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UserInfo:
    """Resolved identity for a gateway/CLI sender.

    Returned by :meth:`IntegrationModule.resolve_user`. ``authorized``
    gates access; everything else is descriptive context the rest of the
    module (tools / context / skills / logging) can key off of.
    """

    user_id: str
    platform: str = ""
    name: str = ""
    authorized: bool = False
    # Free-form attributes from the backend (org id, role, plan, etc.).
    attributes: Dict[str, Any] = field(default_factory=dict)


class IntegrationModule(ABC):
    """One swappable unit: identity + MCP + context + skills + logging."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this module (e.g. 'cocso')."""

    # -- Setup ---------------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the module is configured and ready.

        Called to decide whether to activate the module. Should only
        check config / installed deps / env presence — no network calls.
        """

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Return the config fields this module needs for setup.

        Each field is a dict (see ``MemoryProvider.get_config_schema`` for
        the full key list): ``key``, ``description``, ``secret``,
        ``required``, ``default``, ``choices``, ``url``, ``env_var``.

        Return empty list if no interactive setup is needed.
        """
        return []

    def save_config(self, values: Dict[str, Any], talaria_home: str) -> None:
        """Persist non-secret setup values (secrets go to .env).

        Default no-op for env-only modules.
        """

    # -- MCP info ------------------------------------------------------------

    @abstractmethod
    def mcp_url(self) -> str:
        """Return the MCP endpoint URL (typically read from an env var)."""

    @abstractmethod
    def mcp_key(self) -> str:
        """Return the MCP auth key/token (typically read from an env var)."""

    def extra_mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """Return additional MCP server configs to register alongside the primary.

        Lets a module ship more than one MCP server as part of its bundle
        (e.g. a domain-specific service + a generic filesystem server) without
        forcing the operator to also declare them under ``mcp_servers:`` in
        config.yaml.

        Each value follows the same schema as ``mcp_servers.<name>``:
        ``url`` + ``headers`` for HTTP transport, or ``command`` + ``args``
        + ``env`` for stdio. The primary entry returned by ``mcp_url()`` /
        ``mcp_key()`` wins on name collision (it is registered last).

        Default: no extras.
        """
        return {}

    # -- Identity gateway ----------------------------------------------------

    @abstractmethod
    def resolve_user(
        self,
        *,
        mcp_key: str,
        platform: str,
        user_id: str,
        user_name: str = "",
        **kwargs,
    ) -> UserInfo:
        """Resolve a sender to a :class:`UserInfo`.

        Given the inbound credentials, return identity + authorization.
        Set ``UserInfo.authorized = False`` to deny access. Extra inbound
        context (chat_id, etc.) arrives via ``kwargs``.
        """

    @abstractmethod
    def available_tools(self, user: UserInfo) -> Optional[List[str]]:
        """Return the MCP tool names this user is allowed to call.

        Three distinct return values:

        - ``None``  → no restriction; expose every registered MCP tool.
        - ``[]``    → zero MCP tools; the user may use built-ins only.
        - ``[...]`` → restrict to exactly these tool names (intersected with
          the MCP tools actually registered).

        Built-in (non-MCP) tools are never affected by this list.
        """

    # -- Context files -------------------------------------------------------

    def context_files(self, user: UserInfo) -> List[str]:
        """Return paths of extra context files to inject for this user.

        Default: none.
        """
        return []

    # -- Skills --------------------------------------------------------------

    def skills(self, user: UserInfo) -> List[str]:
        """Return skill names to auto-load for this user.

        Default: none.
        """
        return []

    # -- Logger --------------------------------------------------------------

    def log_message(self, user: Optional[UserInfo], text: str, **ctx) -> None:
        """Record an inbound user message. Default no-op."""

    def log_response(self, user: Optional[UserInfo], text: str, **ctx) -> None:
        """Record an outbound agent response. Default no-op."""

    # -- Lifecycle -----------------------------------------------------------

    def initialize(self, **kwargs) -> None:
        """Optional warm-up (open connections, caches). Default no-op."""

    def shutdown(self) -> None:
        """Optional clean shutdown (flush logs, close connections)."""
