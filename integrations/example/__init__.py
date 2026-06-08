"""Example integration module — a complete, runnable reference.

Bundles identity + MCP info + tools + context files + skills + logging into
one swappable unit. Copy this directory, rename it, swap the env-var prefix,
and adapt :meth:`ExampleModule.resolve_user` to your own backend.

It works in two modes:

1. **HTTP identity backend** — set ``EXAMPLE_IDENTITY_URL``. Each inbound
   sender is resolved by POSTing to that endpoint; the JSON response drives
   authorization, tools, context files, and skills (contract below).

2. **Local demo** (no identity URL) — authorization comes from a static
   ``EXAMPLE_ALLOWED_USERS`` allowlist and every authorized user gets all
   tools. Lets you exercise the wiring end-to-end without a real service.

Activate via config.yaml::

    integration:
      module: example

Env vars::

    EXAMPLE_MCP_URL        (required)  MCP endpoint URL
    EXAMPLE_MCP_KEY        (required)  MCP / identity bearer key
    EXAMPLE_IDENTITY_URL   (optional)  identity-resolution endpoint
    EXAMPLE_ALLOWED_USERS  (optional)  demo allowlist, e.g. "alice,bob" or "*"
    EXAMPLE_MEMORY_TURNS   (optional)  recent turns to recall per user (default 20)
    EXAMPLE_MEMORY_CAP     (optional)  max turns retained per user (default 1000, 0=∞)
    EXAMPLE_HTTP_TIMEOUT   (optional)  identity HTTP timeout seconds (default 3)

Per-user memory (method B): every turn is captured per ``user_id`` and a
recent-history digest is injected on each new session via context_files().
Keying on the sender (not the chat) means it works in DMs *and* groups.

Known limitations (this reference impl):
  - The MCP server is registered once at startup with the module-level
    ``mcp_key()`` — a single service credential, not a per-user key.
  - In group chats the recall digest is injected only when the session is
    first created (per the gateway's new-session model), so it reflects the
    session-creator. Per-turn capture is still per-sender.

Identity HTTP contract — request::

    POST {EXAMPLE_IDENTITY_URL}
    Authorization: Bearer {EXAMPLE_MCP_KEY}
    {"platform": "telegram", "user_id": "123", "user_name": "Alice"}

Response (all fields optional except ``authorized``)::

    {
      "authorized": true,
      "name": "Alice",
      "tools": ["search", "lookup"],   // or "*" for all
      "context_files": ["/data/alice/brief.md"],
      "skills": ["triage"],
      "attributes": {"org_id": "acme", "role": "admin"}
    }
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.integration_module import IntegrationModule, UserInfo

logger = logging.getLogger(__name__)


def _http_timeout() -> float:
    """Identity HTTP timeout (seconds).

    Kept short by default: resolve_user runs on a cache miss inside the
    gateway's async path, so a long block would stall other chats. Tune via
    EXAMPLE_HTTP_TIMEOUT.
    """
    try:
        return float(os.getenv("EXAMPLE_HTTP_TIMEOUT", "3.0"))
    except ValueError:
        return 3.0


class ExampleModule(IntegrationModule):
    @property
    def name(self) -> str:
        return "example"

    # -- Setup ---------------------------------------------------------------

    def is_available(self) -> bool:
        return bool(os.getenv("EXAMPLE_MCP_URL") and os.getenv("EXAMPLE_MCP_KEY"))

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "mcp_url",
                "description": "MCP endpoint URL",
                "required": True,
                "env_var": "EXAMPLE_MCP_URL",
            },
            {
                "key": "mcp_key",
                "description": "MCP / identity bearer key",
                "secret": True,
                "required": True,
                "env_var": "EXAMPLE_MCP_KEY",
            },
            {
                "key": "identity_url",
                "description": "Identity-resolution endpoint (blank = local demo allowlist)",
                "required": False,
                "env_var": "EXAMPLE_IDENTITY_URL",
            },
            {
                "key": "allowed_users",
                "description": "Demo allowlist when no identity URL (comma list, or *)",
                "required": False,
                "env_var": "EXAMPLE_ALLOWED_USERS",
            },
        ]

    # -- MCP info ------------------------------------------------------------

    def mcp_url(self) -> str:
        return os.getenv("EXAMPLE_MCP_URL", "")

    def mcp_key(self) -> str:
        return os.getenv("EXAMPLE_MCP_KEY", "")

    # -- Identity gateway ----------------------------------------------------

    def resolve_user(
        self,
        *,
        mcp_key: str,
        platform: str,
        user_id: str,
        user_name: str = "",
        **kwargs,
    ) -> UserInfo:
        identity_url = os.getenv("EXAMPLE_IDENTITY_URL", "").strip()
        if identity_url:
            return self._resolve_via_http(
                identity_url, mcp_key, platform, user_id, user_name
            )
        return self._resolve_local_demo(platform, user_id, user_name)

    def _resolve_via_http(
        self, url: str, mcp_key: str, platform: str, user_id: str, user_name: str
    ) -> UserInfo:
        """Resolve identity against the HTTP backend (see module docstring)."""
        try:
            import httpx

            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {mcp_key}"},
                json={"platform": platform, "user_id": user_id, "user_name": user_name},
                timeout=_http_timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # Fail closed: an unreachable identity service must not authorize.
            logger.warning("Identity lookup failed for %s/%s: %s", platform, user_id, exc)
            return UserInfo(user_id=user_id, platform=platform, name=user_name, authorized=False)

        attrs = dict(data.get("attributes") or {})
        # Stash response-driven lists in attributes so the methods below can
        # read them back per user without another network call.
        attrs["_tools"] = data.get("tools", "*")
        attrs["_context_files"] = data.get("context_files") or []
        attrs["_skills"] = data.get("skills") or []
        return UserInfo(
            user_id=user_id,
            platform=platform,
            name=data.get("name") or user_name,
            authorized=bool(data.get("authorized")),
            attributes=attrs,
        )

    def _resolve_local_demo(self, platform: str, user_id: str, user_name: str) -> UserInfo:
        """No identity URL: authorize from a static allowlist, grant all tools."""
        raw = os.getenv("EXAMPLE_ALLOWED_USERS", "").strip()
        allowed = {u.strip() for u in raw.split(",") if u.strip()}
        ok = bool(allowed) and ("*" in allowed or user_id in allowed)
        return UserInfo(
            user_id=user_id,
            platform=platform,
            name=user_name,
            authorized=ok,
            attributes={"_tools": "*", "_context_files": [], "_skills": []},
        )

    def available_tools(self, user: UserInfo) -> Optional[List[str]]:
        tools = user.attributes.get("_tools", "*")
        if tools == "*":
            return None  # None = no restriction (expose all registered MCP)
        return list(tools) if isinstance(tools, (list, tuple)) else []

    # -- Context / skills ----------------------------------------------------

    def context_files(self, user: UserInfo) -> List[str]:
        """Backend-supplied files + the per-user memory recall (method B).

        The memory recall is a freshly-written markdown digest of what we've
        stored about this user, so each new session starts with the agent
        already aware of past turns.
        """
        files = list(user.attributes.get("_context_files") or [])
        recall = self._write_recall(user)
        if recall:
            files.append(recall)
        return files

    def skills(self, user: UserInfo) -> List[str]:
        return list(user.attributes.get("_skills") or [])

    # -- Per-user memory (method B) -----------------------------------------
    #
    # Capture: log_message / log_response append each turn to a per-user JSONL
    #          store, keyed by user_id (works in DMs *and* groups, since it
    #          keys on the sender, not the chat).
    # Recall:  context_files() reads the recent turns back, formats a short
    #          markdown digest, and injects it on each new session.

    @staticmethod
    def _safe_key(user_id: str) -> str:
        import re

        return re.sub(r"[^A-Za-z0-9_.-]", "_", str(user_id or "anon"))[:128]

    def _mem_dir(self):
        from talaria_constants import get_talaria_home

        d = get_talaria_home() / "integration-memory" / "example"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _mem_path(self, user_id: str):
        return self._mem_dir() / f"{self._safe_key(user_id)}.jsonl"

    @staticmethod
    def _mem_cap() -> int:
        """Max turns retained per user (env-tunable). 0 = unbounded."""
        try:
            return int(os.getenv("EXAMPLE_MEMORY_CAP", "1000"))
        except ValueError:
            return 1000

    def _store_turn(self, user: Optional[UserInfo], role: str, text: str) -> None:
        user_id = getattr(user, "user_id", None)
        if not user_id or not text:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "role": role,
            "name": getattr(user, "name", None),
            "platform": getattr(user, "platform", None),
            "text": text,
        }
        path = self._mem_path(user_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("Example memory write failed: %s", exc)
            return

        # Bound unbounded growth: trim to the most recent _mem_cap turns.
        # Amortized — only rewrites when the file grows past 1.5× the cap, so
        # it isn't a read+rewrite on every single turn.
        cap = self._mem_cap()
        if cap <= 0:
            return
        try:
            if path.stat().st_size < (cap * 200):  # cheap size pre-check
                return
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > int(cap * 1.5):
                path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.debug("Example memory trim failed: %s", exc)

    def _write_recall(self, user: Optional[UserInfo]) -> Optional[str]:
        """Write a markdown digest of recent turns; return its path (or None)."""
        user_id = getattr(user, "user_id", None)
        if not user_id:
            return None
        path = self._mem_path(user_id)
        if not path.exists():
            return None

        # How many recent turns to recall (env-tunable).
        try:
            n_turns = int(os.getenv("EXAMPLE_MEMORY_TURNS", "20"))
        except ValueError:
            n_turns = 20
        if n_turns <= 0:
            return None

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        recent = lines[-n_turns:]
        if not recent:
            return None

        parts = [f"[Memory — recent history with {getattr(user, 'name', None) or user_id}]"]
        for line in recent:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            role = rec.get("role", "?")
            text = (rec.get("text") or "").strip().replace("\n", " ")
            if len(text) > 400:
                text = text[:400] + "…"
            parts.append(f"- {role}: {text}")

        recall_dir = self._mem_dir() / "recall"
        recall_dir.mkdir(parents=True, exist_ok=True)
        recall_path = recall_dir / f"{self._safe_key(user_id)}.md"
        try:
            recall_path.write_text("\n".join(parts), encoding="utf-8")
        except Exception as exc:
            logger.debug("Example recall write failed: %s", exc)
            return None
        return str(recall_path)

    # -- Logger / capture ----------------------------------------------------

    def log_message(self, user: Optional[UserInfo], text: str, **ctx) -> None:
        self._store_turn(user, "user", text)

    def log_response(self, user: Optional[UserInfo], text: str, **ctx) -> None:
        self._store_turn(user, "assistant", text)


def register(ctx) -> None:
    """Plugin entry point — register the module instance."""
    ctx.register_integration_module(ExampleModule())
