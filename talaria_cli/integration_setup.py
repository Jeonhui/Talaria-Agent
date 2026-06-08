"""talaria integration setup|status — configure the active integration module.

Auto-detects installed integration modules via the plugin system
(``integrations/``), shows a picker, then walks the chosen module's
config schema. Writes activation to config.yaml + secrets to .env.

Running with NO module configured is fully supported: the agent just runs
without identity/MCP/context/skills/logging from a module. The picker
always offers a "None (disabled)" choice.

Mirrors ``talaria_cli/memory_setup.py``.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from talaria_constants import get_talaria_home

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    from talaria_cli.curses_ui import curses_radiolist
    display_items = [f"{label}  {desc}" if desc else label for label, desc in items]
    return curses_radiolist(title, display_items, selected=default, cancel_returns=default)


def _reset_bridge_cache() -> None:
    """Forget the bridge's cached module/identities after a config change.

    No-op if the bridge isn't importable (e.g. minimal CLI context).
    """
    try:
        from gateway.integration_bridge import reset_cache

        reset_cache()
    except Exception:
        pass


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    sys.stdout.write(f"  {label}{suffix}: ")
    sys.stdout.flush()
    if secret and sys.stdin.isatty():
        val = getpass.getpass(prompt="")
    else:
        val = sys.stdin.readline().strip()
    return val or (default or "")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _get_available_modules() -> list:
    """Return ``(name, hint, instance)`` for each discovered module."""
    try:
        from integrations import (
            discover_integration_modules,
            load_integration_module,
        )
        raw = discover_integration_modules()
    except Exception:
        raw = []

    results = []
    for name, _desc, _available in raw:
        try:
            module = load_integration_module(name)
            if not module:
                continue
        except Exception:
            continue

        schema = module.get_config_schema() if hasattr(module, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        if not schema:
            hint = "no setup needed"
        elif has_secrets:
            hint = "requires API key"
        else:
            hint = "local"
        results.append((name, hint, module))
    return results


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup(args) -> None:
    """Interactive integration-module selection and configuration."""
    from talaria_cli.config import load_config, save_config

    modules = _get_available_modules()

    # Picker: every module + an explicit "None (disabled)" option.
    items = [(name, f"— {hint}") for name, hint, _ in modules]
    items.append(("None (disabled)", "— run without an integration module"))
    none_idx = len(items) - 1

    if not modules:
        print("\n  No integration modules detected.")
        print("  Drop one under integrations/<name>/ or ~/.talaria/integrations/.")
        print("  Running without a module is fine — the agent works either way.\n")
        return

    selected = _curses_select("Integration module setup", items, default=none_idx)

    config = load_config()
    if not isinstance(config.get("integration"), dict):
        config["integration"] = {}

    # None / disabled
    if selected >= len(modules) or selected < 0:
        config["integration"]["module"] = ""
        save_config(config)
        _reset_bridge_cache()
        print("\n  ✓ Integration module: none (disabled)")
        print("  Saved to config.yaml\n")
        return

    name, _, module = modules[selected]
    schema = module.get_config_schema() if hasattr(module, "get_config_schema") else []

    module_config = config["integration"].get(name, {})
    if not isinstance(module_config, dict):
        module_config = {}

    env_path = get_talaria_home() / ".env"
    env_writes: dict = {}

    if schema:
        print(f"\n  Configuring {name}:\n")
        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            if choices and not is_secret:
                choice_items = [(c, "") for c in choices]
                current = module_config.get(key, default)
                current_idx = choices.index(current) if current in choices else 0
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                module_config[key] = choices[sel]
            elif is_secret:
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "set"
                    val = _prompt(f"{desc} (current: {masked}, blank to keep)", secret=True)
                else:
                    if url:
                        print(f"  Get yours at {url}")
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                current = module_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    module_config[key] = val
                    if env_var and env_var not in env_writes:
                        env_writes[env_var] = val

    # Activate.
    config["integration"]["module"] = name
    if module_config:
        config["integration"][name] = module_config
    save_config(config)
    _reset_bridge_cache()

    # Non-secret native config.
    talaria_home = str(get_talaria_home())
    if module_config and hasattr(module, "save_config"):
        try:
            module.save_config(module_config, talaria_home)
        except Exception as e:
            print(f"  Failed to write module config: {e}")

    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  Integration module: {name}")
    print("  Activation saved to config.yaml")
    if env_writes:
        print("  Secrets saved to .env")
    print("\n  Start a new session to activate.\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)
    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    """Show the active integration module and what's installed."""
    from talaria_cli.config import load_config

    config = load_config()
    integ = config.get("integration", {}) if isinstance(config.get("integration"), dict) else {}
    active = integ.get("module", "")

    print("\nIntegration status\n" + "─" * 40)
    print(f"  Module:  {active or '(none — disabled)'}")

    modules = _get_available_modules()
    if active:
        match = next((m for m in modules if m[0] == active), None)
        if match:
            _, _, mod = match
            print("  Plugin:  installed ✓")
            ok = mod.is_available()
            print(f"  Status:  {'available ✓' if ok else 'not available ✗'}")
            if not ok:
                schema = mod.get_config_schema() if hasattr(mod, "get_config_schema") else []
                missing = [f for f in schema if f.get("env_var")]
                if missing:
                    print("  Missing:")
                    for f in missing:
                        env_var = f.get("env_var", "")
                        is_set = bool(os.environ.get(env_var))
                        line = f"    {'✓' if is_set else '✗'} {env_var}"
                        if f.get("url") and not is_set:
                            line += f"  → {f['url']}"
                        print(line)
        else:
            print("  Plugin:  NOT installed ✗")

    if modules:
        print("\n  Installed modules:")
        for name, hint, _ in modules:
            mark = " ← active" if name == active else ""
            print(f"    • {name}  ({hint}){mark}")
    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def integration_command(args) -> None:
    sub = getattr(args, "integration_command", None)
    if sub == "setup":
        cmd_setup(args)
    elif sub == "off":
        from talaria_cli.config import load_config, save_config
        config = load_config()
        if not isinstance(config.get("integration"), dict):
            config["integration"] = {}
        config["integration"]["module"] = ""
        save_config(config)
        _reset_bridge_cache()
        print("\n  ✓ Integration module: none (disabled)")
        print("  Saved to config.yaml\n")
    else:
        cmd_status(args)
