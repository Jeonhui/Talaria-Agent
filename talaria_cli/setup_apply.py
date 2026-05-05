"""Detect ``.env`` / ``config.yaml`` changes during setup and prompt the
user to restart the gateway when needed.

Used by ``cmd_setup`` and ``cmd_model`` to bridge the gap between
"settings changed on disk" and "running gateway picked up the change".

Phase 1: classifies the change as ``"reload"`` or ``"restart"`` purely
for the user-facing message, then applies a real ``talaria gateway
restart`` either way. A future phase can swap in an in-process
SIGHUP-driven reload for the ``"reload"`` branch without changing
callers.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, Set, Tuple

# =========================================================================
# Change classification
#
# Anything in ``RESTART_REQUIRED_*`` forces a gateway restart because it
# touches state that can't be hot-swapped (websocket auth, listening
# ports). Everything else is reload-safe — env vars / config sections
# the agent re-reads per request once the process re-imports them.
# =========================================================================

RESTART_REQUIRED_ENV: Set[str] = {
    "DISCORD_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_SECRET",
    "DISCORD_ALLOWED_USERS",
    "TELEGRAM_ALLOWED_USERS",
    "SLACK_ALLOWED_USERS",
    "TALARIA_GATEWAY_PORT",
}

RESTART_REQUIRED_CONFIG_TOP_KEYS: Set[str] = {
    "gateway",
}


def _read_env_file() -> Dict[str, str]:
    """Parse ``~/.talaria/.env`` into a flat ``{KEY: VALUE}`` snapshot."""
    try:
        from talaria_cli.config import get_env_path
        path = get_env_path()
    except Exception:
        return {}
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _load_config_dict() -> Dict[str, Any]:
    """Snapshot ``~/.talaria/config.yaml`` as a dict (empty on failure)."""
    try:
        from talaria_cli.config import load_config
        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def snapshot_setup_state() -> Dict[str, Any]:
    """Capture .env + config.yaml state for later diffing."""
    return {
        "env": _read_env_file(),
        "config": _load_config_dict(),
    }


def classify_setup_changes(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Tuple[str, Set[str], Set[str]]:
    """Return ``(action, changed_env_keys, changed_config_top_keys)``.

    ``action`` is one of ``"none"``, ``"reload"``, ``"restart"``.
    """
    before_env = before.get("env", {}) or {}
    after_env = after.get("env", {}) or {}
    changed_env: Set[str] = {
        k for k in (set(before_env) | set(after_env))
        if before_env.get(k) != after_env.get(k)
    }

    before_cfg = before.get("config", {}) or {}
    after_cfg = after.get("config", {}) or {}
    changed_cfg: Set[str] = {
        k for k in (set(before_cfg) | set(after_cfg))
        if before_cfg.get(k) != after_cfg.get(k)
    }

    if not changed_env and not changed_cfg:
        return "none", changed_env, changed_cfg

    needs_restart = (
        bool(changed_env & RESTART_REQUIRED_ENV)
        or bool(changed_cfg & RESTART_REQUIRED_CONFIG_TOP_KEYS)
    )
    return ("restart" if needs_restart else "reload"), changed_env, changed_cfg


def _format_summary(env_changed: Set[str], cfg_changed: Set[str]) -> str:
    parts = []
    if env_changed:
        sample = sorted(env_changed)
        if len(sample) > 3:
            shown = ", ".join(sample[:3]) + f" +{len(sample) - 3} more"
        else:
            shown = ", ".join(sample)
        parts.append(f"env: {shown}")
    if cfg_changed:
        parts.append(f"config: {', '.join(sorted(cfg_changed))}")
    return "; ".join(parts) if parts else "no changes"


def apply_setup_changes(
    before: Dict[str, Any],
    *,
    prompt: bool = True,
) -> None:
    """Compare current state to ``before`` and offer to restart the gateway.

    Silent no-op when:
    - no relevant changes
    - gateway isn't running
    """
    try:
        from talaria_cli.gateway import _is_service_running
    except Exception:
        return
    try:
        if not _is_service_running():
            return
    except Exception:
        return

    after = snapshot_setup_state()
    action, env_changed, cfg_changed = classify_setup_changes(before, after)
    if action == "none":
        return

    summary = _format_summary(env_changed, cfg_changed)

    print()
    if action == "reload":
        print(f"  Gateway is running. Detected changes ({summary}).")
        print(f"  These can be picked up by reloading the gateway.")
        verb = "reload"
    else:
        print(f"  Gateway is running. Detected changes ({summary}).")
        print(f"  These require a full restart (bot tokens / ports / platforms).")
        verb = "restart"

    if prompt:
        try:
            choice = input(f"  {verb.capitalize()} now? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            choice = "n"
            print()
        if choice in ("n", "no"):
            print(f"  Skipped. Run `talaria gateway restart` later to apply.")
            return

    # Phase 1: both reload and restart map to a real restart for safety.
    # A future SIGHUP-driven in-process reload can replace this branch
    # for the "reload" action without changing the caller contract.
    cmd = [sys.executable, "-m", "talaria_cli.main", "gateway", "restart"]
    try:
        result = subprocess.run(cmd, capture_output=False)
    except Exception as exc:
        print(f"  ⚠ Gateway {verb} failed: {exc}")
        print(f"  Run `talaria gateway restart` manually.")
        return
    if result.returncode != 0:
        print(f"  ⚠ Gateway {verb} returned exit code {result.returncode}.")
        print(f"  Run `talaria gateway restart` manually if needed.")
