#!/usr/bin/env python3
"""
Talaria CLI - Main entry point.

Usage:
    talaria -q "..."            # One-shot answer
    talaria gateway run         # Run gateway in foreground
    talaria gateway start       # Start gateway as service
    talaria gateway stop        # Stop gateway service
    talaria gateway status      # Show gateway status
    talaria gateway install     # Install gateway service
    talaria gateway uninstall   # Uninstall gateway service
    talaria setup               # Interactive setup wizard
    talaria logout              # Clear stored authentication
    talaria status              # Show status of all components
    talaria cron                # Manage cron jobs
    talaria cron list           # List cron jobs
    talaria cron status         # Check if cron scheduler is running
    talaria doctor              # Check configuration and dependencies
    talaria version             # Show version
    talaria update              # Update to latest version
    talaria uninstall           # Uninstall Talaria Agent
    talaria sessions browse     # Interactive session picker with search

NAVIGATION (file is ~3.1K lines — line numbers approximate)
  L38-235     parser helpers, profile override, time / provider checks
  L350-378    cmd_chat              — interactive (removed) → redirect printer
  L386        cmd_gateway           — gateway subcommands
  L394        cmd_setup             — first-run setup
  L406        cmd_model             — model selection (delegates to provider_flows)
  L429-583    small handlers: logout / auth / status / cron / webhook / slack /
              hooks / doctor / dump / debug / config / backup / import / version / uninstall
  L592        cmd_profile           — profile management
  L880        cmd_completion        — shell completion
  L893        cmd_logs              — log viewer
  L914        def main() — argparse build + dispatch

Modules pulled out for clarity:
  talaria_cli/provider_flows.py  — select_provider_and_model + all
                                    _model_flow_* / _aux_* / OAuth helpers
  talaria_cli/sessions.py        — _session_browse_picker (curses) and
                                    _coalesce_session_name_args
  talaria_cli/update_runtime.py  — cmd_update + npm install / fork detect /
                                    git stash / hangup protection / pre-update
                                    backup helpers (~2K LOC)
"""

import argparse
import os
import sys
from pathlib import Path

# Re-exported for backward compatibility with callers that import from
# ``talaria_cli.main`` (talaria_cli/setup.py, talaria_cli/fallback_cmd.py).
from talaria_cli.provider_flows import select_provider_and_model  # noqa: F401
from talaria_cli.sessions import (
    _coalesce_session_name_args,
    _relative_time,
    _session_browse_picker,
)

# ``cmd_update`` lives in update_runtime.py; re-imported so argparse can
# wire it as the handler for ``talaria update`` without main.py owning
# the (~2K LOC) update implementation.
from talaria_cli.update_runtime import cmd_update  # noqa: F401


def _add_accept_hooks_flag(parser) -> None:
    """Attach the ``--accept-hooks`` flag.  Shared across every agent
    subparser so the flag works regardless of CLI position."""
    parser.add_argument(
        "--accept-hooks",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "Auto-approve unseen shell hooks without a TTY prompt "
            "(equivalent to TALARIA_ACCEPT_HOOKS=1 / hooks_auto_accept: true)."
        ),
    )


def _require_tty(command_name: str) -> None:
    """Exit with a clear error if stdin is not a terminal.

    Interactive TUI commands (talaria tools, talaria setup, talaria model) use
    curses or input() prompts that spin at 100% CPU when stdin is a pipe.
    This guard prevents accidental non-interactive invocation.
    """
    if not sys.stdin.isatty():
        print(
            f"Error: 'talaria {command_name}' requires an interactive terminal.\n"
            f"It cannot be run through a pipe or non-interactive subprocess.\n"
            f"Run it directly in your terminal instead.",
            file=sys.stderr,
        )
        sys.exit(1)


# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Profile override — MUST happen before any talaria module import.
#
# Many modules cache TALARIA_HOME at import time (module-level constants).
# We intercept --profile/-p from sys.argv here and set the env var so that
# every subsequent ``os.getenv("TALARIA_HOME", ...)`` resolves correctly.
# The flag is stripped from sys.argv so argparse never sees it.
# Falls back to ~/.talaria/active_profile for sticky default.
# ---------------------------------------------------------------------------
def _apply_profile_override() -> None:
    """Pre-parse --profile/-p and set TALARIA_HOME before module imports."""
    argv = sys.argv[1:]
    profile_name = None
    consume = 0

    # 1. Check for explicit -p / --profile flag
    for i, arg in enumerate(argv):
        if arg in ("--profile", "-p") and i + 1 < len(argv):
            profile_name = argv[i + 1]
            consume = 2
            break
        elif arg.startswith("--profile="):
            profile_name = arg.split("=", 1)[1]
            consume = 1
            break

    # 1.5 If TALARIA_HOME is already set and no explicit flag was given, trust it.
    # This lets child processes (relaunch, subprocess) inherit the parent's
    # profile choice without having to pass --profile again.
    if profile_name is None and os.environ.get("TALARIA_HOME"):
        return

    # 2. If no flag, check active_profile in the talaria root
    if profile_name is None:
        try:
            from talaria_constants import get_default_talaria_root

            active_path = get_default_talaria_root() / "active_profile"
            if active_path.exists():
                name = active_path.read_text().strip()
                if name and name != "default":
                    profile_name = name
                    consume = 0  # don't strip anything from argv
        except (UnicodeDecodeError, OSError):
            pass  # corrupted file, skip

    # 3. If we found a profile, resolve and set TALARIA_HOME
    if profile_name is not None:
        try:
            from talaria_cli.profiles import resolve_profile_env

            talaria_home = resolve_profile_env(profile_name)
        except (ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            # A bug in profiles.py must NEVER prevent talaria from starting
            print(
                f"Warning: profile override failed ({exc}), using default",
                file=sys.stderr,
            )
            return
        os.environ["TALARIA_HOME"] = talaria_home
        # Strip the flag from argv so argparse doesn't choke
        if consume > 0:
            for i, arg in enumerate(argv):
                if arg in ("--profile", "-p"):
                    start = i + 1  # +1 because argv is sys.argv[1:]
                    sys.argv = sys.argv[:start] + sys.argv[start + consume :]
                    break
                elif arg.startswith("--profile="):
                    start = i + 1
                    sys.argv = sys.argv[:start] + sys.argv[start + 1 :]
                    break


_apply_profile_override()

# Load .env from ~/.talaria/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from talaria_cli.branding import (
    DEFAULT_REPO_HTTPS_URL,
    default_branding,
)
from talaria_cli.config import get_talaria_home
from talaria_cli.env_loader import load_talaria_dotenv

load_talaria_dotenv(project_env=PROJECT_ROOT / ".env")

# Bridge security.redact_secrets from config.yaml → TALARIA_REDACT_SECRETS env
# var BEFORE talaria_logging imports agent.redact (which snapshots the flag at
# module-import time). Without this, config.yaml's toggle is ignored because
# the setup_logging() call below imports agent.redact, which reads the env var
# exactly once. Env var in .env still wins — this is config.yaml fallback only.
try:
    if "TALARIA_REDACT_SECRETS" not in os.environ:
        import yaml as _yaml_early
        _cfg_path = get_talaria_home() / "config.yaml"
        if _cfg_path.exists():
            with open(_cfg_path, encoding="utf-8") as _f:
                _early_sec_cfg = (_yaml_early.safe_load(_f) or {}).get("security", {})
            if isinstance(_early_sec_cfg, dict):
                _early_redact = _early_sec_cfg.get("redact_secrets")
                if _early_redact is not None:
                    os.environ["TALARIA_REDACT_SECRETS"] = str(_early_redact).lower()
            del _early_sec_cfg
        del _cfg_path
except Exception:
    pass  # best-effort — redaction stays at default (enabled) on config errors

# Initialize centralized file logging early — all `talaria` subcommands
# (chat, setup, gateway, config, etc.) write to agent.log + errors.log.
try:
    from talaria_logging import setup_logging as _setup_logging

    _setup_logging(mode="cli")
except Exception:
    pass  # best-effort — don't crash the CLI if logging setup fails

# Apply IPv4 preference early, before any HTTP clients are created.
try:
    from talaria_cli.config import load_config as _load_config_early
    from talaria_constants import apply_ipv4_preference as _apply_ipv4

    _early_cfg = _load_config_early()
    _net = _early_cfg.get("network", {})
    if isinstance(_net, dict) and _net.get("force_ipv4"):
        _apply_ipv4(force=True)
    del _early_cfg, _net
except Exception:
    pass  # best-effort — don't crash if config isn't available yet

import logging

from talaria_cli import __release_date__, __version__

logger = logging.getLogger(__name__)


def _has_any_provider_configured() -> bool:
    """Check if at least one inference provider is usable."""
    from talaria_cli.auth import get_auth_status

    # Determine whether Talaria itself has been explicitly configured (model
    # in config that isn't the hardcoded default). Used below to gate external
    # tool credentials (Claude Code, Codex CLI) that shouldn't silently skip
    # the setup wizard on a fresh install.
    from talaria_cli.config import DEFAULT_CONFIG, get_env_path, get_talaria_home, load_config

    _DEFAULT_MODEL = DEFAULT_CONFIG.get("model", "")
    cfg = load_config()
    model_cfg = cfg.get("model")
    if isinstance(model_cfg, dict):
        _model_name = (model_cfg.get("default") or "").strip()
    elif isinstance(model_cfg, str):
        _model_name = model_cfg.strip()
    else:
        _model_name = ""
    _has_talaria_config = _model_name and _model_name != _DEFAULT_MODEL

    # Check env vars (may be set by .env or shell).
    # OPENAI_BASE_URL alone counts — local models (vLLM, llama.cpp, etc.)
    # often don't require an API key.
    from talaria_cli.auth import PROVIDER_REGISTRY

    # Collect all provider env vars
    provider_env_vars = {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "OPENAI_BASE_URL",
    }
    for pconfig in PROVIDER_REGISTRY.values():
        if pconfig.auth_type == "api_key":
            provider_env_vars.update(pconfig.api_key_env_vars)
    if any(os.getenv(v) for v in provider_env_vars):
        return True

    # Check .env file for keys
    env_file = get_env_path()
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip().strip("'\"")
                if key.strip() in provider_env_vars and val:
                    return True
        except Exception:
            pass

    # Check provider-specific auth fallbacks (for example, Copilot via gh auth).
    try:
        for provider_id, pconfig in PROVIDER_REGISTRY.items():
            if pconfig.auth_type != "api_key":
                continue
            status = get_auth_status(provider_id)
            if status.get("logged_in"):
                return True
    except Exception:
        pass

    # Check for OAuth credentials in auth.json
    auth_file = get_talaria_home() / "auth.json"
    if auth_file.exists():
        try:
            import json

            auth = json.loads(auth_file.read_text())
            active = auth.get("active_provider")
            if active:
                status = get_auth_status(active)
                if status.get("logged_in"):
                    return True
        except Exception:
            pass

    # Check config.yaml — if model is a dict with an explicit provider set,
    # the user has gone through setup (fresh installs have model as a plain
    # string).  Also covers custom endpoints that store api_key/base_url in
    # config rather than .env.
    if isinstance(model_cfg, dict):
        cfg_provider = (model_cfg.get("provider") or "").strip()
        cfg_base_url = (model_cfg.get("base_url") or "").strip()
        cfg_api_key = (model_cfg.get("api_key") or "").strip()
        if cfg_provider or cfg_base_url or cfg_api_key:
            return True

    # Check for Claude Code OAuth credentials (~/.claude/.credentials.json)
    # Only count these if Talaria has been explicitly configured — Claude Code
    # being installed doesn't mean the user wants Talaria to use their tokens.
    if _has_talaria_config:
        try:
            from agent.anthropic_adapter import (
                is_claude_code_token_valid,
                read_claude_code_credentials,
            )

            creds = read_claude_code_credentials()
            if creds and (
                is_claude_code_token_valid(creds) or creds.get("refreshToken")
            ):
                return True
        except Exception:
            pass

    return False



def cmd_chat(args):
    """Interactive terminal chat was removed; this is now a thin shim.

    The old REPL (cli.py) is gone. Talaria runs headless / gateway only.
    ``-q/--query`` is preserved as an alias for one-shot mode so existing
    ``talaria -q "..."`` usage keeps working; everything else prints guidance.
    """
    # Preserve `talaria -q "..."` as a headless single-shot query, routed to
    # the standalone one-shot path (run_oneshot bypasses the removed REPL).
    query = getattr(args, "query", None)
    if query:
        # --source still tags the session for filtering, even one-shot.
        if getattr(args, "source", None):
            os.environ["TALARIA_SESSION_SOURCE"] = args.source
        from talaria_cli.oneshot import run_oneshot

        sys.exit(run_oneshot(
            query,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
        ))

    print()
    print("Interactive terminal chat has been removed from this build.")
    print()
    print("Use one of:")
    print('  talaria -q "your question"     One-shot answer (also: --oneshot / -z)')
    print("  talaria gateway run            Chat via Discord / Slack / Telegram")
    print()
    print("Run 'talaria --help' for all commands.")
    sys.exit(0)


def cmd_gateway(args):
    """Gateway management commands."""
    from talaria_cli.gateway import gateway_command

    gateway_command(args)



def cmd_setup(args):
    """Interactive setup wizard."""
    from talaria_cli.setup import run_setup_wizard
    from talaria_cli.setup_apply import apply_setup_changes, snapshot_setup_state

    before = snapshot_setup_state()
    try:
        run_setup_wizard(args)
    finally:
        apply_setup_changes(before)


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    _require_tty("model")
    from talaria_cli.setup_apply import apply_setup_changes, snapshot_setup_state

    before = snapshot_setup_state()
    try:
        select_provider_and_model(args=args)
    finally:
        apply_setup_changes(before)



def cmd_logout(args):
    """Clear provider authentication."""
    from talaria_cli.auth import logout_command

    logout_command(args)


def cmd_auth(args):
    """Manage pooled credentials."""
    from talaria_cli.auth_commands import auth_command

    auth_command(args)


def cmd_status(args):
    """Show status of all components."""
    from talaria_cli.status import show_status

    show_status(args)


def cmd_cron(args):
    """Cron job management."""
    from talaria_cli.cron import cron_command

    cron_command(args)


def cmd_webhook(args):
    """Webhook subscription management."""
    from talaria_cli.webhook import webhook_command

    webhook_command(args)


def cmd_slack(args):
    """Slack integration helpers.

    Dispatches ``talaria slack <subcommand>``. Currently supports:
      manifest — print or write a Slack app manifest with every gateway
                 command registered as a first-class slash.
    """
    sub = getattr(args, "slack_command", None)
    if sub in (None, ""):
        # No subcommand — print usage hint.
        print(
            "usage: talaria slack <subcommand>\n"
            "\n"
            "subcommands:\n"
            "  manifest   Generate a Slack app manifest with every gateway\n"
            "             command registered as a native slash\n"
            "\n"
            "Run `talaria slack manifest -h` for details.",
            file=sys.stderr,
        )
        return 1

    if sub == "manifest":
        from talaria_cli.slack_cli import slack_manifest_command

        return slack_manifest_command(args)

    print(f"Unknown slack subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_hooks(args):
    """Shell-hook inspection and management."""
    from talaria_cli.hooks import hooks_command
    hooks_command(args)


def cmd_doctor(args):
    """Check configuration and dependencies."""
    from talaria_cli.doctor import run_doctor

    run_doctor(args)


def cmd_dump(args):
    """Dump setup summary for support/debugging."""
    from talaria_cli.dump import run_dump

    run_dump(args)


def cmd_debug(args):
    """Debug tools (share report, etc.)."""
    from talaria_cli.debug import run_debug

    run_debug(args)


def cmd_config(args):
    """Configuration management."""
    from talaria_cli.config import config_command

    config_command(args)


def cmd_backup(args):
    """Back up Talaria home directory to a zip file."""
    if getattr(args, "quick", False):
        from talaria_cli.backup import run_quick_backup

        run_quick_backup(args)
    else:
        from talaria_cli.backup import run_backup

        run_backup(args)


def cmd_import(args):
    """Restore a Talaria backup from a zip file."""
    from talaria_cli.backup import run_import

    run_import(args)


def cmd_version(args):
    """Show version."""
    print(f"{default_branding('agent_name', 'Talaria Agent')} v{__version__} ({__release_date__})")
    print(f"Project: {PROJECT_ROOT}")

    # Show Python version
    print(f"Python: {sys.version.split()[0]}")

    # Check for key dependencies
    try:
        import openai

        print(f"OpenAI SDK: {openai.__version__}")
    except ImportError:
        print("OpenAI SDK: Not installed")

    # Show update status (synchronous — acceptable since user asked for version info)
    try:
        from talaria_cli.banner import check_for_updates
        from talaria_cli.config import recommended_update_command

        behind = check_for_updates()
        if behind and behind > 0:
            commits_word = "commit" if behind == 1 else "commits"
            print(
                f"Update available: {behind} {commits_word} behind — "
                f"run '{recommended_update_command()}'"
            )
        elif behind == 0:
            print("Up to date")
    except Exception:
        pass


def cmd_uninstall(args):
    """Uninstall Talaria Agent."""
    _require_tty("uninstall")
    from talaria_cli.uninstall import run_uninstall

    run_uninstall(args)




def cmd_profile(args):
    """Profile management — create, delete, list, switch, alias."""
    from talaria_cli.profiles import (
        _get_wrapper_dir,
        _is_wrapper_dir_in_path,
        check_alias_collision,
        create_profile,
        create_wrapper_script,
        delete_profile,
        get_active_profile_name,
        list_profiles,
        remove_wrapper_script,
        seed_profile_skills,
        set_active_profile,
    )
    from talaria_constants import display_talaria_home

    action = getattr(args, "profile_action", None)

    if action is None:
        # Bare `talaria profile` — show current profile status
        profile_name = get_active_profile_name()
        dhh = display_talaria_home()
        print(f"\nActive profile: {profile_name}")
        print(f"Path:           {dhh}")

        profiles = list_profiles()
        for p in profiles:
            if p.name == profile_name or (profile_name == "default" and p.is_default):
                if p.model:
                    print(
                        f"Model:          {p.model}"
                        + (f" ({p.provider})" if p.provider else "")
                    )
                print(
                    f"Gateway:        {'running' if p.gateway_running else 'stopped'}"
                )
                print(f"Skills:         {p.skill_count} installed")
                if p.alias_path:
                    print(f"Alias:          {p.name} → talaria -p {p.name}")
                break
        print()
        return

    if action == "list":
        profiles = list_profiles()
        active = get_active_profile_name()

        if not profiles:
            print("No profiles found.")
            return

        # Header
        print(f"\n {'Profile':<16} {'Model':<28} {'Gateway':<12} {'Alias'}")
        print(f" {'─' * 15}    {'─' * 27}    {'─' * 11}    {'─' * 12}")

        for p in profiles:
            marker = (
                " ◆"
                if (p.name == active or (active == "default" and p.is_default))
                else "  "
            )
            name = p.name
            model = (p.model or "—")[:26]
            gw = "running" if p.gateway_running else "stopped"
            alias = p.name if p.alias_path else "—"
            if p.is_default:
                alias = "—"
            print(f"{marker}{name:<15} {model:<28} {gw:<12} {alias}")
        print()

    elif action == "use":
        name = args.profile_name
        try:
            set_active_profile(name)
            if name == "default":
                print("Switched to: default (~/.talaria)")
            else:
                print(f"Switched to: {name}")
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif action == "create":
        name = args.profile_name
        clone = getattr(args, "clone", False)
        clone_all = getattr(args, "clone_all", False)
        no_alias = getattr(args, "no_alias", False)

        try:
            clone_from = getattr(args, "clone_from", None)

            profile_dir = create_profile(
                name=name,
                clone_from=clone_from,
                clone_all=clone_all,
                clone_config=clone,
                no_alias=no_alias,
            )
            print(f"\nProfile '{name}' created at {profile_dir}")

            if clone or clone_all:
                source_label = (
                    getattr(args, "clone_from", None) or get_active_profile_name()
                )
                if clone_all:
                    print(f"Full copy from {source_label}.")
                else:
                    print(f"Cloned config, .env, SOUL.md from {source_label}.")

            # Seed bundled skills (skip if --clone-all already copied them)
            if not clone_all:
                result = seed_profile_skills(profile_dir)
                if result:
                    copied = len(result.get("copied", []))
                    print(f"{copied} bundled skills synced.")
                else:
                    print(
                        "⚠ Skills could not be seeded. Run `{} update` to retry.".format(
                            name
                        )
                    )

            # Create wrapper alias
            if not no_alias:
                collision = check_alias_collision(name)
                if collision:
                    print(f"\n⚠ Cannot create alias '{name}' — {collision}")
                    print(
                        f"  Choose a custom alias:  talaria profile alias {name} --name <custom>"
                    )
                    print(f"  Or access via flag:     talaria -p {name} chat")
                else:
                    wrapper_path = create_wrapper_script(name)
                    if wrapper_path:
                        print(f"Wrapper created: {wrapper_path}")
                        if not _is_wrapper_dir_in_path():
                            print(f"\n⚠ {_get_wrapper_dir()} is not in your PATH.")
                            print(
                                "  Add to your shell config (~/.bashrc or ~/.zshrc):"
                            )
                            print('    export PATH="$HOME/.local/bin:$PATH"')

            # Profile dir for display
            try:
                profile_dir_display = "~/" + str(profile_dir.relative_to(Path.home()))
            except ValueError:
                profile_dir_display = str(profile_dir)

            # Next steps
            print("\nNext steps:")
            print(f"  {name} setup              Configure API keys and model")
            print(f"  {name} chat               Start chatting")
            print(f"  {name} gateway start      Start the messaging gateway")
            if clone or clone_all:
                print(f"\n  Edit {profile_dir_display}/.env for different API keys")
                print(f"  Edit {profile_dir_display}/SOUL.md for different personality")
            else:
                print(
                    f"\n  ⚠ This profile has no API keys yet. Run '{name} setup' first,"
                )
                print("    or it will inherit keys from your shell environment.")
                print(f"  Edit {profile_dir_display}/SOUL.md to customize personality")
            print()

        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif action == "delete":
        name = args.profile_name
        yes = getattr(args, "yes", False)
        try:
            delete_profile(name, yes=yes)
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif action == "show":
        name = args.profile_name
        from talaria_cli.profiles import (
            _check_gateway_running,
            _count_skills,
            _read_config_model,
            get_profile_dir,
            profile_exists,
        )

        if not profile_exists(name):
            print(f"Error: Profile '{name}' does not exist.")
            sys.exit(1)
        profile_dir = get_profile_dir(name)
        model, provider = _read_config_model(profile_dir)
        gw = _check_gateway_running(profile_dir)
        skills = _count_skills(profile_dir)
        wrapper = _get_wrapper_dir() / name

        print(f"\nProfile: {name}")
        print(f"Path:    {profile_dir}")
        if model:
            print(f"Model:   {model}" + (f" ({provider})" if provider else ""))
        print(f"Gateway: {'running' if gw else 'stopped'}")
        print(f"Skills:  {skills}")
        print(
            f".env:    {'exists' if (profile_dir / '.env').exists() else 'not configured'}"
        )
        print(
            f"SOUL.md: {'exists' if (profile_dir / 'SOUL.md').exists() else 'not configured'}"
        )
        if wrapper.exists():
            print(f"Alias:   {wrapper}")
        print()

    elif action == "alias":
        name = args.profile_name
        remove = getattr(args, "remove", False)
        custom_name = getattr(args, "alias_name", None)

        from talaria_cli.profiles import profile_exists

        if not profile_exists(name):
            print(f"Error: Profile '{name}' does not exist.")
            sys.exit(1)

        alias_name = custom_name or name

        if remove:
            if remove_wrapper_script(alias_name):
                print(f"✓ Removed alias '{alias_name}'")
            else:
                print(f"No alias '{alias_name}' found to remove.")
        else:
            collision = check_alias_collision(alias_name)
            if collision:
                print(f"Error: {collision}")
                sys.exit(1)
            wrapper_path = create_wrapper_script(alias_name)
            if wrapper_path:
                # If custom name, write the profile name into the wrapper
                if custom_name:
                    wrapper_path.write_text(f'#!/bin/sh\nexec talaria -p {name} "$@"\n')
                print(f"✓ Alias created: {wrapper_path}")
                if not _is_wrapper_dir_in_path():
                    print(f"⚠ {_get_wrapper_dir()} is not in your PATH.")

    elif action == "rename":
        from talaria_cli.profiles import rename_profile

        try:
            new_dir = rename_profile(args.old_name, args.new_name)
            print(f"\nProfile renamed: {args.old_name} → {args.new_name}")
            print(f"Path: {new_dir}\n")
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif action == "export":
        from talaria_cli.profiles import export_profile

        name = args.profile_name
        output = args.output or f"{name}.tar.gz"
        try:
            result_path = export_profile(name, output)
            print(f"✓ Exported '{name}' to {result_path}")
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif action == "import":
        from talaria_cli.profiles import import_profile

        try:
            profile_dir = import_profile(
                args.archive, name=getattr(args, "import_name", None)
            )
            name = profile_dir.name
            print(f"✓ Imported profile '{name}' at {profile_dir}")

            # Offer to create alias
            collision = check_alias_collision(name)
            if not collision:
                wrapper_path = create_wrapper_script(name)
                if wrapper_path:
                    print(f"  Wrapper created: {wrapper_path}")
            print()
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)
def cmd_completion(args, parser=None):
    """Print shell completion script."""
    from talaria_cli.completion import generate_bash, generate_fish, generate_zsh

    shell = getattr(args, "shell", "bash")
    if shell == "zsh":
        print(generate_zsh(parser))
    elif shell == "fish":
        print(generate_fish(parser))
    else:
        print(generate_bash(parser))


def cmd_logs(args):
    """View and filter Talaria log files."""
    from talaria_cli.logs import list_logs, tail_log

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )


def main():
    """Main entry point for talaria CLI."""
    from talaria_cli._parser import build_top_level_parser

    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help="Select default model and provider",
        description="Interactively select your inference provider and default model",
    )
    model_parser.add_argument(
        "--portal-url",
        help="Portal base URL for Nous login (default: production portal)",
    )
    model_parser.add_argument(
        "--inference-url",
        help="Inference API base URL for Nous login (default: production inference API)",
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth client id to use for Nous login (default: talaria-cli)",
    )
    model_parser.add_argument(
        "--scope", default=None, help="OAuth scope to request for Nous login"
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically during Nous login",
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds for Nous login (default: 15)",
    )
    model_parser.add_argument(
        "--ca-bundle", help="Path to CA bundle PEM file for Nous TLS verification"
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for Nous login (testing only)",
    )
    model_parser.set_defaults(func=cmd_model)

    # =========================================================================
    # fallback command — manage the fallback provider chain
    # =========================================================================
    from talaria_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback",
        help="Manage fallback providers (tried when the primary model fails)",
        description=(
            "Manage the fallback provider chain.  Fallback providers are tried "
            "in order when the primary model fails with rate-limit, overload, or "
            f"connection errors.  See: {DEFAULT_REPO_HTTPS_URL}"
        ),
    )
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="Show the current fallback chain (default when no subcommand)",
    )
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `talaria model`) and append to the chain",
    )
    fallback_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help="Pick an entry to delete from the chain",
    )
    fallback_subparsers.add_parser(
        "clear",
        help="Remove all fallback entries",
    )
    fallback_parser.set_defaults(func=cmd_fallback)

    # =========================================================================
    # gateway command
    # =========================================================================
    gateway_parser = subparsers.add_parser(
        "gateway",
        help="Messaging gateway management",
        description="Manage the messaging gateway (Telegram, Discord, Slack)",
    )
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_command")

    # gateway run (default)
    gateway_run = gateway_subparsers.add_parser(
        "run", help="Run gateway in foreground (recommended for WSL, Docker, Termux)"
    )
    gateway_run.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase stderr log verbosity (-v=INFO, -vv=DEBUG)",
    )
    gateway_run.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress all stderr log output"
    )
    gateway_run.add_argument(
        "--replace",
        action="store_true",
        help="Replace any existing gateway instance (useful for systemd)",
    )
    _add_accept_hooks_flag(gateway_run)
    _add_accept_hooks_flag(gateway_parser)

    # gateway start
    gateway_start = gateway_subparsers.add_parser(
        "start", help="Start the installed systemd/launchd background service"
    )
    gateway_start.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_start.add_argument(
        "--all",
        action="store_true",
        help="Kill ALL stale gateway processes across all profiles before starting",
    )

    # gateway stop
    gateway_stop = gateway_subparsers.add_parser("stop", help="Stop gateway service")
    gateway_stop.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_stop.add_argument(
        "--all",
        action="store_true",
        help="Stop ALL gateway processes across all profiles",
    )

    # gateway restart
    gateway_restart = gateway_subparsers.add_parser(
        "restart", help="Restart gateway service"
    )
    gateway_restart.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_restart.add_argument(
        "--all",
        action="store_true",
        help="Kill ALL gateway processes across all profiles before restarting",
    )

    # gateway status
    gateway_status = gateway_subparsers.add_parser("status", help="Show gateway status")
    gateway_status.add_argument("--deep", action="store_true", help="Deep status check")
    gateway_status.add_argument(
        "-l",
        "--full",
        action="store_true",
        help="Show full, untruncated service/log output where supported",
    )
    gateway_status.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )

    # gateway install
    gateway_install = gateway_subparsers.add_parser(
        "install", help="Install gateway as a systemd/launchd background service"
    )
    gateway_install.add_argument("--force", action="store_true", help="Force reinstall")
    gateway_install.add_argument(
        "--system",
        action="store_true",
        help="Install as a Linux system-level service (starts at boot)",
    )
    gateway_install.add_argument(
        "--run-as-user",
        dest="run_as_user",
        help="User account the Linux system service should run as",
    )

    # gateway uninstall
    gateway_uninstall = gateway_subparsers.add_parser(
        "uninstall", help="Uninstall gateway service"
    )
    gateway_uninstall.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )

    # gateway setup
    gateway_subparsers.add_parser("setup", help="Configure messaging platforms")

    # gateway migrate-legacy
    gateway_migrate_legacy = gateway_subparsers.add_parser(
        "migrate-legacy",
        help="Remove legacy talaria.service units from pre-rename installs",
        description=(
            "Stop, disable, and remove legacy Talaria gateway unit files "
            "(e.g. talaria.service) left over from older installs. Profile "
            "units (talaria-gateway-<profile>.service) and unrelated "
            "third-party services are never touched."
        ),
    )
    gateway_migrate_legacy.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="List what would be removed without doing it",
    )
    gateway_migrate_legacy.add_argument(
        "-y",
        "--yes",
        dest="yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )

    gateway_parser.set_defaults(func=cmd_gateway)

    # =========================================================================
    # setup command
    # =========================================================================
    setup_parser = subparsers.add_parser(
        "setup",
        help="Interactive setup wizard",
        description=f"Configure {default_branding('agent_name', 'Talaria Agent')} with an interactive wizard. "
        "Run a specific section: talaria setup model|terminal|gateway|tools|agent",
    )
    setup_parser.add_argument(
        "section",
        nargs="?",
        choices=["model", "terminal", "gateway", "tools", "agent"],
        default=None,
        help="Run a specific setup section instead of the full wizard",
    )
    setup_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode (use defaults/env vars)",
    )
    setup_parser.add_argument(
        "--reset", action="store_true", help="Reset configuration to defaults"
    )
    setup_parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="(Default on existing installs.) Re-run the full wizard, "
             "showing current values as defaults. Kept for backwards "
             "compatibility — a bare 'talaria setup' now does this.",
    )
    setup_parser.add_argument(
        "--quick",
        action="store_true",
        help="On existing installs: only prompt for items that are missing "
             "or unset, instead of running the full reconfigure wizard.",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # =========================================================================
    # slack command
    # =========================================================================
    slack_parser = subparsers.add_parser(
        "slack",
        help="Slack integration helpers (manifest generation, etc.)",
        description="Slack integration helpers for Talaria.",
    )
    slack_sub = slack_parser.add_subparsers(dest="slack_command")
    slack_manifest = slack_sub.add_parser(
        "manifest",
        help="Print or write a Slack app manifest with every gateway command "
             "registered as a native slash (/btw, /stop, /model, ...)",
        description=(
            "Generate a Slack app manifest that registers every gateway "
            "command in COMMAND_REGISTRY as a first-class Slack slash "
            "command (matching Discord and Telegram parity). Paste the "
            "output into Slack app config → Features → App Manifest → "
            "Edit, then Save. Reinstall the app if Slack prompts for it."
        ),
    )
    slack_manifest.add_argument(
        "--write",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="Write manifest to a file instead of stdout. With no PATH "
             "writes to $TALARIA_HOME/slack-manifest.json.",
    )
    slack_manifest.add_argument(
        "--name",
        default=None,
        help='Bot display name (default: "Talaria")',
    )
    slack_manifest.add_argument(
        "--description",
        default=None,
        help="Bot description shown in Slack's app directory.",
    )
    slack_manifest.add_argument(
        "--slashes-only",
        action="store_true",
        help="Emit only the features.slash_commands array (for merging "
             "into an existing manifest manually).",
    )
    slack_parser.set_defaults(func=cmd_slack)

    # =========================================================================
    # logout command
    # =========================================================================
    logout_parser = subparsers.add_parser(
        "logout",
        help="Clear authentication for an inference provider",
        description="Remove stored credentials and reset provider config",
    )
    logout_parser.add_argument(
        "--provider",
        choices=["anthropic", "openai-codex"],
        default=None,
        help="Provider to log out from (default: active provider)",
    )
    logout_parser.set_defaults(func=cmd_logout)

    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage pooled provider credentials",
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")
    auth_add = auth_subparsers.add_parser("add", help="Add a pooled credential")
    auth_add.add_argument(
        "provider",
        help="Provider id (for example: anthropic, openai-codex, openai, xiaomi, lmstudio, custom)",
    )
    auth_add.add_argument(
        "--type",
        dest="auth_type",
        choices=["oauth", "api-key", "api_key"],
        help="Credential type to add",
    )
    auth_add.add_argument("--label", help="Optional display label")
    auth_add.add_argument(
        "--api-key", help="API key value (otherwise prompted securely)"
    )
    auth_add.add_argument("--portal-url", help="Nous portal base URL")
    auth_add.add_argument("--inference-url", help="Nous inference base URL")
    auth_add.add_argument("--client-id", help="OAuth client id")
    auth_add.add_argument("--scope", help="OAuth scope override")
    auth_add.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open a browser for OAuth login",
    )
    auth_add.add_argument(
        "--timeout", type=float, help="OAuth/network timeout in seconds"
    )
    auth_add.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for OAuth login",
    )
    auth_add.add_argument("--ca-bundle", help="Custom CA bundle for OAuth login")
    auth_list = auth_subparsers.add_parser("list", help="List pooled credentials")
    auth_list.add_argument("provider", nargs="?", help="Optional provider filter")
    auth_remove = auth_subparsers.add_parser(
        "remove", help="Remove a pooled credential by index, id, or label"
    )
    auth_remove.add_argument("provider", help="Provider id")
    auth_remove.add_argument(
        "target", help="Credential index, entry id, or exact label"
    )
    auth_reset = auth_subparsers.add_parser(
        "reset", help="Clear exhaustion status for all credentials for a provider"
    )
    auth_reset.add_argument("provider", help="Provider id")
    auth_status = auth_subparsers.add_parser("status", help="Show auth status for a provider")
    auth_status.add_argument("provider", help="Provider id")
    auth_logout = auth_subparsers.add_parser("logout", help="Log out a provider and clear stored auth state")
    auth_logout.add_argument("provider", help="Provider id")
    auth_spotify = auth_subparsers.add_parser("spotify", help=f"Authenticate {default_branding('agent_short_name', 'Talaria')} with Spotify via PKCE")
    auth_spotify.add_argument("spotify_action", nargs="?", choices=["login", "status", "logout"], default="login")
    auth_spotify.add_argument("--client-id", help="Spotify app client_id (or set TALARIA_SPOTIFY_CLIENT_ID)")
    auth_spotify.add_argument("--redirect-uri", help="Allow-listed localhost redirect URI for your Spotify app")
    auth_spotify.add_argument("--scope", help="Override requested Spotify scopes")
    auth_spotify.add_argument("--no-browser", action="store_true", help="Do not attempt to open the browser automatically")
    auth_spotify.add_argument("--timeout", type=float, help="Callback/token exchange timeout in seconds")
    auth_parser.set_defaults(func=cmd_auth)

    # =========================================================================
    # status command
    # =========================================================================
    status_parser = subparsers.add_parser(
        "status",
        help="Show status of all components",
        description=f"Display status of {default_branding('agent_name', 'Talaria Agent')} components",
    )
    status_parser.add_argument(
        "--all", action="store_true", help="Show all details (redacted for sharing)"
    )
    status_parser.add_argument(
        "--deep", action="store_true", help="Run deep checks (may take longer)"
    )
    status_parser.set_defaults(func=cmd_status)

    # =========================================================================
    # cron command
    # =========================================================================
    cron_parser = subparsers.add_parser(
        "cron", help="Cron job management", description="Manage scheduled tasks"
    )
    cron_subparsers = cron_parser.add_subparsers(dest="cron_command")

    # cron list
    cron_list = cron_subparsers.add_parser("list", help="List scheduled jobs")
    cron_list.add_argument("--all", action="store_true", help="Include disabled jobs")

    # cron create/add
    cron_create = cron_subparsers.add_parser(
        "create", aliases=["add"], help="Create a scheduled job"
    )
    cron_create.add_argument(
        "schedule", help="Schedule like '30m', 'every 2h', or '0 9 * * *'"
    )
    cron_create.add_argument(
        "prompt", nargs="?", help="Optional self-contained prompt or task instruction"
    )
    cron_create.add_argument("--name", help="Optional human-friendly job name")
    cron_create.add_argument(
        "--deliver",
        help="Delivery target: origin, local, telegram, discord, slack, or platform:chat_id",
    )
    cron_create.add_argument("--repeat", type=int, help="Optional repeat count")
    cron_create.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help="Attach a skill. Repeat to add multiple skills.",
    )
    cron_create.add_argument(
        "--script",
        help="Path to a Python script whose stdout is injected into the prompt each run",
    )
    cron_create.add_argument(
        "--workdir",
        help="Absolute path for the job to run from. Injects AGENTS.md / CLAUDE.md / .cursorrules from that directory and uses it as the cwd for terminal/file/code_exec tools. Omit to preserve old behaviour (no project context files).",
    )

    # cron edit
    cron_edit = cron_subparsers.add_parser(
        "edit", help="Edit an existing scheduled job"
    )
    cron_edit.add_argument("job_id", help="Job ID to edit")
    cron_edit.add_argument("--schedule", help="New schedule")
    cron_edit.add_argument("--prompt", help="New prompt/task instruction")
    cron_edit.add_argument("--name", help="New job name")
    cron_edit.add_argument("--deliver", help="New delivery target")
    cron_edit.add_argument("--repeat", type=int, help="New repeat count")
    cron_edit.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help="Replace the job's skills with this set. Repeat to attach multiple skills.",
    )
    cron_edit.add_argument(
        "--add-skill",
        dest="add_skills",
        action="append",
        help="Append a skill without replacing the existing list. Repeatable.",
    )
    cron_edit.add_argument(
        "--remove-skill",
        dest="remove_skills",
        action="append",
        help="Remove a specific attached skill. Repeatable.",
    )
    cron_edit.add_argument(
        "--clear-skills",
        action="store_true",
        help="Remove all attached skills from the job",
    )
    cron_edit.add_argument(
        "--script",
        help="Path to a Python script whose stdout is injected into the prompt each run. Pass empty string to clear.",
    )
    cron_edit.add_argument(
        "--workdir",
        help="Absolute path for the job to run from (injects AGENTS.md etc. and sets terminal cwd). Pass empty string to clear.",
    )

    # lifecycle actions
    cron_pause = cron_subparsers.add_parser("pause", help="Pause a scheduled job")
    cron_pause.add_argument("job_id", help="Job ID to pause")

    cron_resume = cron_subparsers.add_parser("resume", help="Resume a paused job")
    cron_resume.add_argument("job_id", help="Job ID to resume")

    cron_run = cron_subparsers.add_parser(
        "run", help="Run a job on the next scheduler tick"
    )
    cron_run.add_argument("job_id", help="Job ID to trigger")
    _add_accept_hooks_flag(cron_run)

    cron_remove = cron_subparsers.add_parser(
        "remove", aliases=["rm", "delete"], help="Remove a scheduled job"
    )
    cron_remove.add_argument("job_id", help="Job ID to remove")

    # cron status
    cron_subparsers.add_parser("status", help="Check if cron scheduler is running")

    # cron tick (mostly for debugging)
    cron_tick = cron_subparsers.add_parser("tick", help="Run due jobs once and exit")
    _add_accept_hooks_flag(cron_tick)
    _add_accept_hooks_flag(cron_parser)
    cron_parser.set_defaults(func=cmd_cron)

    # =========================================================================
    # webhook command
    # =========================================================================
    webhook_parser = subparsers.add_parser(
        "webhook",
        help="Manage dynamic webhook subscriptions",
        description="Create, list, and remove webhook subscriptions for event-driven agent activation",
    )
    webhook_subparsers = webhook_parser.add_subparsers(dest="webhook_action")

    wh_sub = webhook_subparsers.add_parser(
        "subscribe", aliases=["add"], help="Create a webhook subscription"
    )
    wh_sub.add_argument("name", help="Route name (used in URL: /webhooks/<name>)")
    wh_sub.add_argument(
        "--prompt", default="", help="Prompt template with {dot.notation} payload refs"
    )
    wh_sub.add_argument(
        "--events", default="", help="Comma-separated event types to accept"
    )
    wh_sub.add_argument("--description", default="", help="What this subscription does")
    wh_sub.add_argument(
        "--skills", default="", help="Comma-separated skill names to load"
    )
    wh_sub.add_argument(
        "--deliver",
        default="log",
        help="Delivery target: log, telegram, discord, slack, etc.",
    )
    wh_sub.add_argument(
        "--deliver-chat-id",
        default="",
        help="Target chat ID for cross-platform delivery",
    )
    wh_sub.add_argument(
        "--secret", default="", help="HMAC secret (auto-generated if omitted)"
    )
    wh_sub.add_argument(
        "--deliver-only",
        action="store_true",
        help="Skip the agent — deliver the rendered prompt directly as the "
        "message. Zero LLM cost. Requires --deliver to be a real target "
        "(not 'log').",
    )

    webhook_subparsers.add_parser(
        "list", aliases=["ls"], help="List all dynamic subscriptions"
    )

    wh_rm = webhook_subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove a subscription"
    )
    wh_rm.add_argument("name", help="Subscription name to remove")

    wh_test = webhook_subparsers.add_parser(
        "test", help="Send a test POST to a webhook route"
    )
    wh_test.add_argument("name", help="Subscription name to test")
    wh_test.add_argument(
        "--payload", default="", help="JSON payload to send (default: test payload)"
    )

    webhook_parser.set_defaults(func=cmd_webhook)

    # =========================================================================
    # hooks command — shell-hook inspection and management
    # =========================================================================
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Inspect and manage shell-script hooks",
        description=(
            "Inspect shell-script hooks declared in ~/.talaria/config.yaml, "
            "test them against synthetic payloads, and manage the first-use "
            "consent allowlist at ~/.talaria/shell-hooks-allowlist.json."
        ),
    )
    hooks_subparsers = hooks_parser.add_subparsers(dest="hooks_action")

    hooks_subparsers.add_parser(
        "list", aliases=["ls"],
        help="List configured hooks with matcher, timeout, and consent status",
    )

    _hk_test = hooks_subparsers.add_parser(
        "test",
        help="Fire every hook matching <event> against a synthetic payload",
    )
    _hk_test.add_argument(
        "event",
        help="Hook event name (e.g. pre_tool_call, pre_llm_call, subagent_stop)",
    )
    _hk_test.add_argument(
        "--for-tool", dest="for_tool", default=None,
        help=(
            "Only fire hooks whose matcher matches this tool name "
            "(used for pre_tool_call / post_tool_call)"
        ),
    )
    _hk_test.add_argument(
        "--payload-file", dest="payload_file", default=None,
        help=(
            "Path to a JSON file whose contents are merged into the "
            "synthetic payload before execution"
        ),
    )

    _hk_revoke = hooks_subparsers.add_parser(
        "revoke", aliases=["remove", "rm"],
        help="Remove a command's allowlist entries (takes effect on next restart)",
    )
    _hk_revoke.add_argument(
        "command",
        help="The exact command string to revoke (as declared in config.yaml)",
    )

    hooks_subparsers.add_parser(
        "doctor",
        help=(
            "Check each configured hook: exec bit, allowlist, mtime drift, "
            "JSON validity, and synthetic run timing"
        ),
    )

    hooks_parser.set_defaults(func=cmd_hooks)

    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and dependencies",
        description=f"Diagnose issues with {default_branding('agent_name', 'Talaria Agent')} setup",
    )
    doctor_parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues automatically"
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    # =========================================================================
    # dump command
    # =========================================================================
    dump_parser = subparsers.add_parser(
        "dump",
        help="Dump setup summary for support/debugging",
        description=f"Output a compact, plain-text summary of your {default_branding('agent_short_name', 'Talaria')} setup "
        "that can be copy-pasted into Discord/GitHub for support context",
    )
    dump_parser.add_argument(
        "--show-keys",
        action="store_true",
        help="Show redacted API key prefixes (first/last 4 chars) instead of just set/not set",
    )
    dump_parser.set_defaults(func=cmd_dump)

    # =========================================================================
    # debug command
    # =========================================================================
    debug_parser = subparsers.add_parser(
        "debug",
        help="Debug tools — upload logs and system info for support",
        description="Debug utilities for Talaria Agent. Use 'talaria debug share' to "
        "upload a debug report (system info + recent logs) to a paste "
        "service and get a shareable URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    talaria debug share              Upload debug report and print URL
    talaria debug share --lines 500  Include more log lines
    talaria debug share --expire 30  Keep paste for 30 days
    talaria debug share --local      Print report locally (no upload)
    talaria debug delete <url>       Delete a previously uploaded paste
""",
    )
    debug_sub = debug_parser.add_subparsers(dest="debug_command")
    share_parser = debug_sub.add_parser(
        "share",
        help="Upload debug report to a paste service and print a shareable URL",
    )
    share_parser.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Number of log lines to include per log file (default: 200)",
    )
    share_parser.add_argument(
        "--expire",
        type=int,
        default=7,
        help="Paste expiry in days (default: 7)",
    )
    share_parser.add_argument(
        "--local",
        action="store_true",
        help="Print the report locally instead of uploading",
    )
    delete_parser = debug_sub.add_parser(
        "delete",
        help="Delete a paste uploaded by 'talaria debug share'",
    )
    delete_parser.add_argument(
        "urls",
        nargs="*",
        default=[],
        help="One or more paste URLs to delete (e.g. https://paste.rs/abc123)",
    )
    debug_parser.set_defaults(func=cmd_debug)

    # =========================================================================
    # backup command
    # =========================================================================
    backup_parser = subparsers.add_parser(
        "backup",
        help="Back up Talaria home directory to a zip file",
        description="Create a zip archive of your entire Talaria configuration, "
        "skills, sessions, and data (excludes the talaria-agent codebase). "
        "Use --quick for a fast snapshot of just critical state files.",
    )
    backup_parser.add_argument(
        "-o",
        "--output",
        help="Output path for the zip file (default: ~/talaria-backup-<timestamp>.zip)",
    )
    backup_parser.add_argument(
        "-q",
        "--quick",
        action="store_true",
        help="Quick snapshot: only critical state files (config, state.db, .env, auth, cron)",
    )
    backup_parser.add_argument(
        "-l", "--label", help="Label for the snapshot (only used with --quick)"
    )
    backup_parser.set_defaults(func=cmd_backup)

    # =========================================================================
    # import command
    # =========================================================================
    import_parser = subparsers.add_parser(
        "import",
        help="Restore a Talaria backup from a zip file",
        description="Extract a previously created Talaria backup into your "
        "Talaria home directory, restoring configuration, skills, "
        "sessions, and data",
    )
    import_parser.add_argument("zipfile", help="Path to the backup zip file")
    import_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing files without confirmation",
    )
    import_parser.set_defaults(func=cmd_import)

    # =========================================================================
    # config command
    # =========================================================================
    config_parser = subparsers.add_parser(
        "config",
        help="View and edit configuration",
        description="Manage Talaria Agent configuration",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    # config show (default)
    config_subparsers.add_parser("show", help="Show current configuration")

    # config edit
    config_subparsers.add_parser("edit", help="Open config file in editor")

    # config set
    config_set = config_subparsers.add_parser("set", help="Set a configuration value")
    config_set.add_argument(
        "key", nargs="?", help="Configuration key (e.g., model, terminal.backend)"
    )
    config_set.add_argument("value", nargs="?", help="Value to set")

    # config path
    config_subparsers.add_parser("path", help="Print config file path")

    # config env-path
    config_subparsers.add_parser("env-path", help="Print .env file path")

    # config check
    config_subparsers.add_parser("check", help="Check for missing/outdated config")

    # config migrate
    config_subparsers.add_parser("migrate", help="Update config with new options")

    config_parser.set_defaults(func=cmd_config)

    # =========================================================================
    # pairing command
    # =========================================================================
    pairing_parser = subparsers.add_parser(
        "pairing",
        help="Manage DM pairing codes for user authorization",
        description="Approve or revoke user access via pairing codes",
    )
    pairing_sub = pairing_parser.add_subparsers(dest="pairing_action")

    pairing_sub.add_parser("list", help="Show pending + approved users")

    pairing_approve_parser = pairing_sub.add_parser(
        "approve", help="Approve a pairing code"
    )
    pairing_approve_parser.add_argument(
        "platform", help="Platform name (telegram, discord, slack)"
    )
    pairing_approve_parser.add_argument("code", help="Pairing code to approve")

    pairing_revoke_parser = pairing_sub.add_parser("revoke", help="Revoke user access")
    pairing_revoke_parser.add_argument("platform", help="Platform name")
    pairing_revoke_parser.add_argument("user_id", help="User ID to revoke")

    pairing_sub.add_parser("clear-pending", help="Clear all pending codes")

    def cmd_pairing(args):
        from talaria_cli.pairing import pairing_command

        pairing_command(args)

    pairing_parser.set_defaults(func=cmd_pairing)

    # =========================================================================
    # skills command
    # =========================================================================
    skills_parser = subparsers.add_parser(
        "skills",
        help="Search, install, configure, and manage skills",
        description="Search, install, inspect, audit, configure, and manage skills from skills.sh, well-known agent skill endpoints, GitHub, ClawHub, and other registries.",
    )
    skills_subparsers = skills_parser.add_subparsers(dest="skills_action")

    skills_browse = skills_subparsers.add_parser(
        "browse", help="Browse all available skills (paginated)"
    )
    skills_browse.add_argument(
        "--page", type=int, default=1, help="Page number (default: 1)"
    )
    skills_browse.add_argument(
        "--size", type=int, default=20, help="Results per page (default: 20)"
    )
    skills_browse.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
        ],
        help="Filter by source (default: all)",
    )

    skills_search = skills_subparsers.add_parser(
        "search", help="Search skill registries"
    )
    skills_search.add_argument("query", help="Search query")
    skills_search.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
        ],
    )
    skills_search.add_argument("--limit", type=int, default=10, help="Max results")

    skills_install = skills_subparsers.add_parser("install", help="Install a skill")
    skills_install.add_argument(
        "identifier",
        help="Skill identifier (e.g. openai/skills/skill-creator) or a direct HTTP(S) URL to a SKILL.md file",
    )
    skills_install.add_argument(
        "--category", default="", help="Category folder to install into"
    )
    skills_install.add_argument(
        "--name",
        default="",
        help="Override the skill name (useful when installing from a URL whose SKILL.md has no `name:` frontmatter)",
    )
    skills_install.add_argument(
        "--force", action="store_true", help="Install despite blocked scan verdict"
    )
    skills_install.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt (needed in TUI mode)",
    )

    skills_inspect = skills_subparsers.add_parser(
        "inspect", help="Preview a skill without installing"
    )
    skills_inspect.add_argument("identifier", help="Skill identifier")

    skills_list = skills_subparsers.add_parser("list", help="List installed skills")
    skills_list.add_argument(
        "--source", default="all", choices=["all", "hub", "builtin", "local"]
    )
    skills_list.add_argument(
        "--enabled-only",
        action="store_true",
        help="Hide disabled skills. Use with -p <profile> to see exactly "
             "which skills will load for that profile.",
    )

    skills_check = skills_subparsers.add_parser(
        "check", help="Check installed hub skills for updates"
    )
    skills_check.add_argument(
        "name", nargs="?", help="Specific skill to check (default: all)"
    )

    skills_update = skills_subparsers.add_parser(
        "update", help="Update installed hub skills"
    )
    skills_update.add_argument(
        "name",
        nargs="?",
        help="Specific skill to update (default: all outdated skills)",
    )

    skills_audit = skills_subparsers.add_parser(
        "audit", help="Re-scan installed hub skills"
    )
    skills_audit.add_argument(
        "name", nargs="?", help="Specific skill to audit (default: all)"
    )

    skills_uninstall = skills_subparsers.add_parser(
        "uninstall", help="Remove a hub-installed skill"
    )
    skills_uninstall.add_argument("name", help="Skill name to remove")

    skills_reset = skills_subparsers.add_parser(
        "reset",
        help="Reset a bundled skill — clears 'user-modified' tracking so updates work again",
        description=(
            "Clear a bundled skill's entry from the sync manifest (~/.talaria/skills/.bundled_manifest) "
            "so future 'talaria update' runs stop marking it as user-modified. Pass --restore to also "
            "replace the current copy with the bundled version."
        ),
    )
    skills_reset.add_argument(
        "name", help="Skill name to reset (e.g. google-workspace)"
    )
    skills_reset.add_argument(
        "--restore",
        action="store_true",
        help="Also delete the current copy and re-copy the bundled version",
    )
    skills_reset.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt when using --restore",
    )

    skills_publish = skills_subparsers.add_parser(
        "publish", help="Publish a skill to a registry"
    )
    skills_publish.add_argument("skill_path", help="Path to skill directory")
    skills_publish.add_argument(
        "--to", default="github", choices=["github", "clawhub"], help="Target registry"
    )
    skills_publish.add_argument(
        "--repo", default="", help="Target GitHub repo (e.g. openai/skills)"
    )

    skills_snapshot = skills_subparsers.add_parser(
        "snapshot", help="Export/import skill configurations"
    )
    snapshot_subparsers = skills_snapshot.add_subparsers(dest="snapshot_action")
    snap_export = snapshot_subparsers.add_parser(
        "export", help="Export installed skills to a file"
    )
    snap_export.add_argument("output", help="Output JSON file path (use - for stdout)")
    snap_import = snapshot_subparsers.add_parser(
        "import", help="Import and install skills from a file"
    )
    snap_import.add_argument("input", help="Input JSON file path")
    snap_import.add_argument(
        "--force", action="store_true", help="Force install despite caution verdict"
    )

    skills_tap = skills_subparsers.add_parser("tap", help="Manage skill sources")
    tap_subparsers = skills_tap.add_subparsers(dest="tap_action")
    tap_subparsers.add_parser("list", help="List configured taps")
    tap_add = tap_subparsers.add_parser("add", help="Add a GitHub repo as skill source")
    tap_add.add_argument("repo", help="GitHub repo (e.g. owner/repo)")
    tap_rm = tap_subparsers.add_parser("remove", help="Remove a tap")
    tap_rm.add_argument("name", help="Tap name to remove")

    # config sub-action: interactive enable/disable
    skills_subparsers.add_parser(
        "config",
        help="Interactive skill configuration — enable/disable individual skills",
    )

    def cmd_skills(args):
        # Route 'config' action to skills_config module
        if getattr(args, "skills_action", None) == "config":
            _require_tty("skills config")
            from talaria_cli.skills_config import skills_command as skills_config_command

            skills_config_command(args)
        else:
            from talaria_cli.skills_hub import skills_command

            skills_command(args)

    skills_parser.set_defaults(func=cmd_skills)

    # =========================================================================
    # plugins command
    # =========================================================================
    plugins_parser = subparsers.add_parser(
        "plugins",
        help="Manage plugins — install, update, remove, list",
        description="Install plugins from Git repositories, update, remove, or list them.",
    )
    plugins_subparsers = plugins_parser.add_subparsers(dest="plugins_action")

    plugins_install = plugins_subparsers.add_parser(
        "install", help="Install a plugin from a Git URL or owner/repo"
    )
    plugins_install.add_argument(
        "identifier",
        help="Git URL or owner/repo shorthand (e.g. anpicasso/talaria-plugin-chrome-profiles)",
    )
    plugins_install.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Remove existing plugin and reinstall",
    )
    _install_enable_group = plugins_install.add_mutually_exclusive_group()
    _install_enable_group.add_argument(
        "--enable",
        action="store_true",
        help="Auto-enable the plugin after install (skip confirmation prompt)",
    )
    _install_enable_group.add_argument(
        "--no-enable",
        action="store_true",
        help="Install disabled (skip confirmation prompt); enable later with `talaria plugins enable <name>`",
    )

    plugins_update = plugins_subparsers.add_parser(
        "update", help="Pull latest changes for an installed plugin"
    )
    plugins_update.add_argument("name", help="Plugin name to update")

    plugins_remove = plugins_subparsers.add_parser(
        "remove", aliases=["rm", "uninstall"], help="Remove an installed plugin"
    )
    plugins_remove.add_argument("name", help="Plugin directory name to remove")

    plugins_subparsers.add_parser("list", aliases=["ls"], help="List installed plugins")

    plugins_enable = plugins_subparsers.add_parser(
        "enable", help="Enable a disabled plugin"
    )
    plugins_enable.add_argument("name", help="Plugin name to enable")

    plugins_disable = plugins_subparsers.add_parser(
        "disable", help="Disable a plugin without removing it"
    )
    plugins_disable.add_argument("name", help="Plugin name to disable")

    def cmd_plugins(args):
        from talaria_cli.plugins_cmd import plugins_command

        plugins_command(args)

    plugins_parser.set_defaults(func=cmd_plugins)

    # =========================================================================
    # Plugin CLI commands — dynamically registered by memory/general plugins.
    # Plugins provide a register_cli(subparser) function that builds their
    # own argparse tree.  No hardcoded plugin commands in main.py.
    # =========================================================================
    try:
        from plugins.memory import discover_plugin_cli_commands

        for cmd_info in discover_plugin_cli_commands():
            plugin_parser = subparsers.add_parser(
                cmd_info["name"],
                help=cmd_info["help"],
                description=cmd_info.get("description", ""),
                formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
            )
            cmd_info["setup_fn"](plugin_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)

    # =========================================================================
    # curator command — background skill maintenance
    # =========================================================================
    curator_parser = subparsers.add_parser(
        "curator",
        help="Background skill maintenance (curator) — status, run, pause, pin",
        description=(
            "The curator is an auxiliary-model background task that "
            "periodically reviews agent-created skills, prunes stale ones, "
            "consolidates overlaps, and archives obsolete skills. "
            "Bundled and hub-installed skills are never touched. "
            "Archives are recoverable; auto-deletion never happens."
        ),
    )
    try:
        from talaria_cli.curator import register_cli as _register_curator_cli
        _register_curator_cli(curator_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("curator CLI wiring failed: %s", _exc)

    # =========================================================================
    # memory command
    # =========================================================================
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Install providers via 'talaria plugins install <repo>'.\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )

    def cmd_memory(args):
        sub = getattr(args, "memory_command", None)
        if sub == "off":
            from talaria_cli.config import load_config, save_config

            config = load_config()
            if not isinstance(config.get("memory"), dict):
                config["memory"] = {}
            config["memory"]["provider"] = ""
            save_config(config)
            print("\n  ✓ Memory provider: built-in only")
            print("  Saved to config.yaml\n")
        elif sub == "reset":
            from talaria_constants import display_talaria_home, get_talaria_home

            mem_dir = get_talaria_home() / "memories"
            target = getattr(args, "target", "all")
            files_to_reset = []
            if target in ("all", "memory"):
                files_to_reset.append(("MEMORY.md", "agent notes"))
            if target in ("all", "user"):
                files_to_reset.append(("USER.md", "user profile"))

            # Check what exists
            existing = [
                (f, desc) for f, desc in files_to_reset if (mem_dir / f).exists()
            ]
            if not existing:
                print(
                    f"\n  Nothing to reset — no memory files found in {display_talaria_home()}/memories/\n"
                )
                return

            print("\n  This will permanently erase the following memory files:")
            for f, desc in existing:
                path = mem_dir / f
                size = path.stat().st_size
                print(f"    ◆ {f} ({desc}) — {size:,} bytes")

            if not getattr(args, "yes", False):
                try:
                    answer = input("\n  Type 'yes' to confirm: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\n  Cancelled.\n")
                    return
                if answer != "yes":
                    print("  Cancelled.\n")
                    return

            for f, desc in existing:
                (mem_dir / f).unlink()
                print(f"  ✓ Deleted {f} ({desc})")

            print(
                "\n  Memory reset complete. New sessions will start with a blank slate."
            )
            print(f"  Files were in: {display_talaria_home()}/memories/\n")
        else:
            from talaria_cli.memory_setup import memory_command

            memory_command(args)

    memory_parser.set_defaults(func=cmd_memory)

    # =========================================================================
    # integration command
    # =========================================================================
    integration_parser = subparsers.add_parser(
        "integration",
        help="Configure the active integration module",
        description=(
            "Set up and manage the active integration module.\n\n"
            "A module bundles identity, MCP info, available tools, context\n"
            "files, skills, and logging into one swappable unit. Only one is\n"
            "active at a time. Running with none is fully supported."
        ),
    )
    integration_sub = integration_parser.add_subparsers(dest="integration_command")
    integration_sub.add_parser(
        "setup", help="Interactive module selection and configuration"
    )
    integration_sub.add_parser("status", help="Show the active integration module")
    integration_sub.add_parser("off", help="Disable the integration module")

    def cmd_integration(args):
        from talaria_cli.integration_setup import integration_command

        integration_command(args)

    integration_parser.set_defaults(func=cmd_integration)

    # =========================================================================
    # skin command — icon / branding / colors
    # =========================================================================
    skin_parser = subparsers.add_parser(
        "skin",
        help="Manage icon / branding / colors (skins)",
        description=(
            "List, switch, inspect, and scaffold skins.\n\n"
            "A skin is a YAML file controlling the icon/emoji, agent name,\n"
            "colors, prompt symbol, and spinner. Scaffold one with\n"
            "'talaria skin new <name>', edit it, then 'talaria skin set <name>'."
        ),
    )
    skin_sub = skin_parser.add_subparsers(dest="skin_action")
    skin_sub.add_parser("list", help="List built-in and user skins")
    skin_sub.add_parser("show", help="Show the active skin's branding and colors")
    _skin_set = skin_sub.add_parser("set", help="Activate a skin (persists to config)")
    _skin_set.add_argument("name", help="Skin name")
    _skin_new = skin_sub.add_parser("new", help="Scaffold a new skin YAML")
    _skin_new.add_argument("name", help="New skin name (lowercase/hyphens)")
    _skin_new.add_argument("--from", dest="from_skin", default=None,
                           help="Seed from an existing skin (default: default)")
    _skin_new.add_argument("--force", action="store_true", help="Overwrite if it exists")
    _skin_path = skin_sub.add_parser("path", help="Print a skin's YAML path")
    _skin_path.add_argument("name", nargs="?", default=None, help="Skin name (default: active)")

    def cmd_skin(args):
        from talaria_cli.skin_cli import skin_command

        skin_command(args)

    skin_parser.set_defaults(func=cmd_skin)

    # =========================================================================
    # tools command
    # =========================================================================
    tools_parser = subparsers.add_parser(
        "tools",
        help="Configure which tools are enabled per platform",
        description=(
            "Enable, disable, or list tools for CLI, Telegram, Discord, etc.\n\n"
            "Built-in toolsets use plain names (e.g. web, memory).\n"
            "MCP tools use server:tool notation (e.g. github:create_issue).\n\n"
            "Run 'talaria tools' with no subcommand for the interactive configuration UI."
        ),
    )
    tools_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of enabled tools per platform and exit",
    )
    tools_sub = tools_parser.add_subparsers(dest="tools_action")

    # talaria tools list [--platform cli]
    tools_list_p = tools_sub.add_parser(
        "list",
        help="Show all tools and their enabled/disabled status",
    )
    tools_list_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to show (default: cli)",
    )

    # talaria tools disable <name...> [--platform cli]
    tools_disable_p = tools_sub.add_parser(
        "disable",
        help="Disable toolsets or MCP tools",
    )
    tools_disable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help="Toolset name (e.g. web) or MCP tool in server:tool form",
    )
    tools_disable_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to apply to (default: cli)",
    )

    # talaria tools enable <name...> [--platform cli]
    tools_enable_p = tools_sub.add_parser(
        "enable",
        help="Enable toolsets or MCP tools",
    )
    tools_enable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help="Toolset name or MCP tool in server:tool form",
    )
    tools_enable_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to apply to (default: cli)",
    )

    def cmd_tools(args):
        action = getattr(args, "tools_action", None)
        if action in ("list", "disable", "enable"):
            from talaria_cli.tools_config import tools_disable_enable_command

            tools_disable_enable_command(args)
        else:
            _require_tty("tools")
            from talaria_cli.tools_config import tools_command

            tools_command(args)

    tools_parser.set_defaults(func=cmd_tools)
    # =========================================================================
    # mcp command — manage MCP server connections
    # =========================================================================
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Manage MCP servers and run Talaria as an MCP server",
        description=(
            "Manage MCP server connections and run Talaria as an MCP server.\n\n"
            "MCP servers provide additional tools via the Model Context Protocol.\n"
            "Use 'talaria mcp add' to connect to a new server, or\n"
            "'talaria mcp serve' to expose Talaria conversations over MCP."
        ),
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_serve_p = mcp_sub.add_parser(
        "serve",
        help="Run Talaria as an MCP server (expose conversations to other agents)",
    )
    mcp_serve_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging on stderr",
    )
    _add_accept_hooks_flag(mcp_serve_p)

    mcp_add_p = mcp_sub.add_parser(
        "add", help="Add an MCP server (discovery-first install)"
    )
    mcp_add_p.add_argument("name", help="Server name (used as config key)")
    mcp_add_p.add_argument("--url", help="HTTP/SSE endpoint URL")
    mcp_add_p.add_argument("--command", help="Stdio command (e.g. npx)")
    mcp_add_p.add_argument(
        "--args", nargs="*", default=[], help="Arguments for stdio command"
    )
    mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help="Auth method")
    mcp_add_p.add_argument(
        "--env",
        nargs="*",
        default=[],
        help="Environment variables for stdio servers (KEY=VALUE)",
    )

    mcp_rm_p = mcp_sub.add_parser("remove", aliases=["rm"], help="Remove an MCP server")
    mcp_rm_p.add_argument("name", help="Server name to remove")

    mcp_sub.add_parser("list", aliases=["ls"], help="List configured MCP servers")

    mcp_test_p = mcp_sub.add_parser("test", help="Test MCP server connection")
    mcp_test_p.add_argument("name", help="Server name to test")

    mcp_cfg_p = mcp_sub.add_parser(
        "configure", aliases=["config"], help="Toggle tool selection"
    )
    mcp_cfg_p.add_argument("name", help="Server name to configure")

    mcp_login_p = mcp_sub.add_parser(
        "login",
        help="Force re-authentication for an OAuth-based MCP server",
    )
    mcp_login_p.add_argument("name", help="Server name to re-authenticate")

    _add_accept_hooks_flag(mcp_parser)

    def cmd_mcp(args):
        from talaria_cli.mcp_config import mcp_command

        mcp_command(args)

    mcp_parser.set_defaults(func=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="Manage session history (list, rename, export, prune, delete)",
        description="View and manage the SQLite session store",
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
    sessions_list.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_list.add_argument(
        "--limit", type=int, default=20, help="Max sessions to show"
    )

    sessions_export = sessions_subparsers.add_parser(
        "export", help="Export sessions to a JSONL file"
    )
    sessions_export.add_argument(
        "output", help="Output JSONL file path (use - for stdout)"
    )
    sessions_export.add_argument("--source", help="Filter by source")
    sessions_export.add_argument("--session-id", help="Export a specific session")

    sessions_delete = sessions_subparsers.add_parser(
        "delete", help="Delete a specific session"
    )
    sessions_delete.add_argument("session_id", help="Session ID to delete")
    sessions_delete.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
    sessions_prune.add_argument(
        "--older-than",
        type=int,
        default=90,
        help="Delete sessions older than N days (default: 90)",
    )
    sessions_prune.add_argument("--source", help="Only prune sessions from this source")
    sessions_prune.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_subparsers.add_parser("stats", help="Show session store statistics")

    sessions_status = sessions_subparsers.add_parser(
        "status",
        help="Show active sessions and live MCP connection status",
        description=(
            "Snapshot of currently-active (not-yet-ended) sessions plus a live "
            "probe of each configured MCP server's connection."
        ),
    )
    sessions_status.add_argument(
        "--source", help="Filter sessions by source (cli, telegram, discord, etc.)"
    )
    sessions_status.add_argument(
        "--limit", type=int, default=20, help="Max sessions to show (default: 20)"
    )
    sessions_status.add_argument(
        "--all",
        action="store_true",
        help="Include ended sessions, not just active ones",
    )
    sessions_status.add_argument(
        "--no-mcp",
        action="store_true",
        help="Skip probing MCP servers (faster)",
    )

    sessions_rename = sessions_subparsers.add_parser(
        "rename", help="Set or change a session's title"
    )
    sessions_rename.add_argument("session_id", help="Session ID to rename")
    sessions_rename.add_argument("title", nargs="+", help="New title for the session")

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help="Interactive session picker — browse, search, and resume sessions",
    )
    sessions_browse.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_browse.add_argument(
        "--limit", type=int, default=500, help="Max sessions to load (default: 500)"
    )

    def _confirm_prompt(prompt: str) -> bool:
        """Prompt for y/N confirmation, safe against non-TTY environments."""
        try:
            return input(prompt).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def cmd_sessions(args):
        import json as _json

        try:
            from talaria_state import SessionDB

            db = SessionDB()
        except Exception as e:
            print(f"Error: Could not open session database: {e}")
            return

        action = args.sessions_action

        # Hide third-party tool sessions by default, but honour explicit --source
        _source = getattr(args, "source", None)
        _exclude = None if _source else ["tool"]

        if action == "list":
            sessions = db.list_sessions_rich(
                source=args.source, exclude_sources=_exclude, limit=args.limit
            )
            if not sessions:
                print("No sessions found.")
                return
            has_titles = any(s.get("title") for s in sessions)
            if has_titles:
                print(f"{'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
                print("─" * 110)
            else:
                print(f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}")
                print("─" * 95)
            for s in sessions:
                last_active = _relative_time(s.get("last_active"))
                preview = (
                    s.get("preview", "")[:38]
                    if has_titles
                    else s.get("preview", "")[:48]
                )
                if has_titles:
                    title = (s.get("title") or "—")[:30]
                    sid = s["id"]
                    print(f"{title:<32} {preview:<40} {last_active:<13} {sid}")
                else:
                    sid = s["id"]
                    print(f"{preview:<50} {last_active:<13} {s['source']:<6} {sid}")

        elif action == "status":
            include_ended = getattr(args, "all", False)
            sessions = db.list_sessions_rich(
                source=args.source, exclude_sources=_exclude, limit=args.limit
            )
            db.close()
            # "Active" = sessions with no recorded end (still open). The DB is
            # the only cross-process signal here — a running gateway records
            # open sessions there, so this works even from a separate process.
            if include_ended:
                shown = sessions
            else:
                shown = [s for s in sessions if not s.get("ended_at")]

            print()
            print("◆ Sessions" + ("" if include_ended else " (active)"))
            if not shown:
                print("  none")
            else:
                print(f"  {'Source':<9} {'Model':<24} {'Msgs':>5}  {'Last Active':<13} ID")
                print("  " + "─" * 74)
                for s in shown:
                    src = (s.get("source") or "?")[:8]
                    model = (s.get("model") or "—")[:23]
                    msgs = s.get("message_count", 0) or 0
                    la = _relative_time(s.get("last_active"))
                    sid = s.get("id", "?")
                    print(f"  {src:<9} {model:<24} {msgs:>5}  {la:<13} {sid}")
                    title = s.get("title")
                    if title:
                        print(f"  {'':<9} ↳ {title[:60]}")

            # MCP connection status — probe each configured server live unless
            # skipped.  The in-process _servers registry is empty from a
            # standalone CLI, so we connect+list+disconnect to report health.
            if not getattr(args, "no_mcp", False):
                print()
                print("◆ MCP servers")
                try:
                    from talaria_cli.mcp_config import (
                        _get_mcp_servers,
                        _probe_single_server,
                    )
                    servers = _get_mcp_servers()
                except Exception as e:
                    servers = {}
                    print(f"  (could not read MCP config: {e})")
                if not servers:
                    print("  none configured")
                else:
                    for name, cfg in servers.items():
                        try:
                            tools = _probe_single_server(name, cfg, connect_timeout=10)
                            n = len(tools)
                            print(f"  ✓ {name}: connected ({n} tool{'s' if n != 1 else ''})")
                        except Exception as e:
                            msg = str(e).strip() or e.__class__.__name__
                            if len(msg) > 70:
                                msg = msg[:67] + "..."
                            print(f"  ✗ {name}: {msg}")
            print()

        elif action == "export":
            if args.session_id:
                resolved_session_id = db.resolve_session_id(args.session_id)
                if not resolved_session_id:
                    print(f"Session '{args.session_id}' not found.")
                    return
                data = db.export_session(resolved_session_id)
                if not data:
                    print(f"Session '{args.session_id}' not found.")
                    return
                line = _json.dumps(data, ensure_ascii=False) + "\n"
                if args.output == "-":

                    sys.stdout.write(line)
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(line)
                    print(f"Exported 1 session to {args.output}")
            else:
                sessions = db.export_all(source=args.source)
                if args.output == "-":

                    for s in sessions:
                        sys.stdout.write(_json.dumps(s, ensure_ascii=False) + "\n")
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        for s in sessions:
                            f.write(_json.dumps(s, ensure_ascii=False) + "\n")
                    print(f"Exported {len(sessions)} sessions to {args.output}")

        elif action == "delete":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete session '{resolved_session_id}' and all its messages? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_talaria_home() / "sessions"
            if db.delete_session(resolved_session_id, sessions_dir=sessions_dir):
                print(f"Deleted session '{resolved_session_id}'.")
            else:
                print(f"Session '{args.session_id}' not found.")

        elif action == "prune":
            days = args.older_than
            source_msg = f" from '{args.source}'" if args.source else ""
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete all ended sessions older than {days} days{source_msg}? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_talaria_home() / "sessions"
            count = db.prune_sessions(older_than_days=days, source=args.source,
                                      sessions_dir=sessions_dir)
            print(f"Pruned {count} session(s).")

        elif action == "rename":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            title = " ".join(args.title)
            try:
                if db.set_session_title(resolved_session_id, title):
                    print(f"Session '{resolved_session_id}' renamed to: {title}")
                else:
                    print(f"Session '{args.session_id}' not found.")
            except ValueError as e:
                print(f"Error: {e}")

        elif action == "browse":
            limit = getattr(args, "limit", 500) or 500
            source = getattr(args, "source", None)
            _browse_exclude = None if source else ["tool"]
            sessions = db.list_sessions_rich(
                source=source, exclude_sources=_browse_exclude, limit=limit
            )
            db.close()
            if not sessions:
                print("No sessions found.")
                return

            selected_id = _session_browse_picker(sessions)
            if not selected_id:
                print("Cancelled.")
                return

            # Launch talaria --resume <id> by replacing the current process
            print(f"Resuming session: {selected_id}")
            from talaria_cli.relaunch import relaunch
            relaunch(["--resume", selected_id])
            return  # won't reach here after execvp

        elif action == "stats":
            total = db.session_count()
            msgs = db.message_count()
            print(f"Total sessions: {total}")
            print(f"Total messages: {msgs}")
            for src in ["cli", "telegram", "discord", "slack"]:
                c = db.session_count(source=src)
                if c > 0:
                    print(f"  {src}: {c} sessions")
            db_path = db.db_path
            if db_path.exists():
                size_mb = os.path.getsize(db_path) / (1024 * 1024)
                print(f"Database size: {size_mb:.1f} MB")

        else:
            sessions_parser.print_help()

        db.close()

    sessions_parser.set_defaults(func=cmd_sessions)

    # =========================================================================
    # insights command
    # =========================================================================
    insights_parser = subparsers.add_parser(
        "insights",
        help="Show usage insights and analytics",
        description="Analyze session history to show token usage, costs, tool patterns, and activity trends",
    )
    insights_parser.add_argument(
        "--days", type=int, default=30, help="Number of days to analyze (default: 30)"
    )
    insights_parser.add_argument(
        "--source", help="Filter by platform (cli, telegram, discord, etc.)"
    )

    def cmd_insights(args):
        try:
            from agent.insights import InsightsEngine
            from talaria_state import SessionDB

            db = SessionDB()
            engine = InsightsEngine(db)
            report = engine.generate(days=args.days, source=args.source)
            print(engine.format_terminal(report))
            db.close()
        except Exception as e:
            print(f"Error generating insights: {e}")

    insights_parser.set_defaults(func=cmd_insights)


    # =========================================================================
    # version command
    # =========================================================================
    version_parser = subparsers.add_parser("version", help="Show version information")
    version_parser.set_defaults(func=cmd_version)

    # =========================================================================
    # update command
    # =========================================================================
    update_parser = subparsers.add_parser(
        "update",
        help="Update Talaria Agent to the latest version",
        description="Pull the latest changes from git and reinstall dependencies",
    )
    update_parser.add_argument(
        "--gateway",
        action="store_true",
        default=False,
        help="Gateway mode: use file-based IPC for prompts instead of stdin (used internally by /update)",
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Check whether an update is available without installing anything",
    )
    update_parser.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help="Skip the pre-update backup for this run (overrides updates.pre_update_backup)",
    )
    update_parser.add_argument(
        "--backup",
        action="store_true",
        default=False,
        help="Force a pre-update backup for this run (off by default; overrides updates.pre_update_backup)",
    )
    update_parser.set_defaults(func=cmd_update)

    # =========================================================================
    # uninstall command
    # =========================================================================
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall Talaria Agent",
        description="Remove Talaria Agent from your system. Can keep configs/data for reinstall.",
    )
    uninstall_parser.add_argument(
        "--full",
        action="store_true",
        help="Full uninstall - remove everything including configs and data",
    )
    uninstall_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompts"
    )
    uninstall_parser.set_defaults(func=cmd_uninstall)

    # =========================================================================
    # profile command
    # =========================================================================
    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage profiles — multiple isolated Talaria instances",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")

    profile_subparsers.add_parser("list", help="List all profiles")
    profile_use = profile_subparsers.add_parser(
        "use", help="Set sticky default profile"
    )
    profile_use.add_argument("profile_name", help="Profile name (or 'default')")

    profile_create = profile_subparsers.add_parser(
        "create", help="Create a new profile"
    )
    profile_create.add_argument(
        "profile_name", help="Profile name (lowercase, alphanumeric)"
    )
    profile_create.add_argument(
        "--clone",
        action="store_true",
        help="Copy config.yaml, .env, SOUL.md from active profile",
    )
    profile_create.add_argument(
        "--clone-all",
        action="store_true",
        help="Full copy of active profile (all state)",
    )
    profile_create.add_argument(
        "--clone-from",
        metavar="SOURCE",
        help="Source profile to clone from (default: active)",
    )
    profile_create.add_argument(
        "--no-alias", action="store_true", help="Skip wrapper script creation"
    )

    profile_delete = profile_subparsers.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("profile_name", help="Profile to delete")
    profile_delete.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt"
    )

    profile_show = profile_subparsers.add_parser("show", help="Show profile details")
    profile_show.add_argument("profile_name", help="Profile to show")

    profile_alias = profile_subparsers.add_parser(
        "alias", help="Manage wrapper scripts"
    )
    profile_alias.add_argument("profile_name", help="Profile name")
    profile_alias.add_argument(
        "--remove", action="store_true", help="Remove the wrapper script"
    )
    profile_alias.add_argument(
        "--name",
        dest="alias_name",
        metavar="NAME",
        help="Custom alias name (default: profile name)",
    )

    profile_rename = profile_subparsers.add_parser("rename", help="Rename a profile")
    profile_rename.add_argument("old_name", help="Current profile name")
    profile_rename.add_argument("new_name", help="New profile name")

    profile_export = profile_subparsers.add_parser(
        "export", help="Export a profile to archive"
    )
    profile_export.add_argument("profile_name", help="Profile to export")
    profile_export.add_argument(
        "-o", "--output", default=None, help="Output file (default: <name>.tar.gz)"
    )

    profile_import = profile_subparsers.add_parser(
        "import", help="Import a profile from archive"
    )
    profile_import.add_argument("archive", help="Path to .tar.gz archive")
    profile_import.add_argument(
        "--name",
        dest="import_name",
        metavar="NAME",
        help="Profile name (default: inferred from archive)",
    )

    profile_parser.set_defaults(func=cmd_profile)

    # =========================================================================
    # completion command
    # =========================================================================
    completion_parser = subparsers.add_parser(
        "completion",
        help="Print shell completion script (bash, zsh, or fish)",
    )
    completion_parser.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help="Shell type (default: bash)",
    )
    completion_parser.set_defaults(func=lambda args: cmd_completion(args, parser))
    # =========================================================================
    # logs command
    # =========================================================================
    logs_parser = subparsers.add_parser(
        "logs",
        help="View and filter Talaria log files",
        description="View, tail, and filter agent.log / errors.log / gateway.log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    talaria logs                    Show last 50 lines of agent.log
    talaria logs -f                 Follow agent.log in real time
    talaria logs errors             Show last 50 lines of errors.log
    talaria logs gateway -n 100     Show last 100 lines of gateway.log
    talaria logs --level WARNING    Only show WARNING and above
    talaria logs --session abc123   Filter by session ID
    talaria logs --component tools  Only show tool-related lines
    talaria logs --since 1h         Lines from the last hour
    talaria logs --since 30m -f     Follow, starting from 30 min ago
    talaria logs list               List available log files with sizes
""",
    )
    logs_parser.add_argument(
        "log_name",
        nargs="?",
        default="agent",
        help="Log to view: agent (default), errors, gateway, or 'list' to show available files",
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow the log in real time (like tail -f)",
    )
    logs_parser.add_argument(
        "--level",
        metavar="LEVEL",
        help="Minimum log level to show (DEBUG, INFO, WARNING, ERROR)",
    )
    logs_parser.add_argument(
        "--session",
        metavar="ID",
        help="Filter lines containing this session ID substring",
    )
    logs_parser.add_argument(
        "--since",
        metavar="TIME",
        help="Show lines since TIME ago (e.g. 1h, 30m, 2d)",
    )
    logs_parser.add_argument(
        "--component",
        metavar="NAME",
        help="Filter by component: gateway, agent, tools, cli, cron",
    )
    logs_parser.set_defaults(func=cmd_logs)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    # =========================================================================
    # agents command — multi-profile fleet control
    # =========================================================================
    agents_parser = subparsers.add_parser(
        "agents",
        help="Run multiple profiles concurrently as independent agents",
        description=(
            "Fleet wrapper over `--profile <name> gateway run`.  Each "
            "profile is already a fully isolated TALARIA_HOME — these "
            "subcommands make it ergonomic to start/stop several at once."
        ),
    )
    from talaria_cli.agents import register_cli as _register_agents_cli
    _register_agents_cli(agents_parser)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    checkpoints_parser = subparsers.add_parser(
        "checkpoints",
        help="Inspect / prune / clear ~/.talaria/checkpoints/",
        description=(
            "Direct visibility and control over the filesystem checkpoint "
            "store.  Show per-project breakdown, see how much disk "
            "space checkpoints occupy, force a prune, or wipe the base."
        ),
    )
    from talaria_cli.checkpoints import register_cli as _register_checkpoints_cli
    _register_checkpoints_cli(checkpoints_parser)

    # =========================================================================
    # security command
    # =========================================================================
    security_parser = subparsers.add_parser(
        "security",
        help="Supply-chain audit against OSV.dev",
        description=(
            "On-demand audit of venv packages, plugin requirements, and "
            "pinned MCP servers against the OSV.dev advisory database."
        ),
    )
    security_subparsers = security_parser.add_subparsers(dest="security_command")
    audit_parser = security_subparsers.add_parser(
        "audit",
        help="Scan installed components for known vulnerabilities",
    )
    for p in (security_parser, audit_parser):
        p.add_argument("--skip-venv", action="store_true",
                       help="Skip scanning the Python venv")
        p.add_argument("--skip-plugins", action="store_true",
                       help="Skip scanning ~/.talaria/plugins requirements")
        p.add_argument("--skip-mcp", action="store_true",
                       help="Skip scanning pinned MCP servers in config.yaml")
        p.add_argument("--json", action="store_true",
                       help="Emit machine-readable JSON output")
        p.add_argument("--fail-on", default="critical",
                       choices=["low", "moderate", "high", "critical"],
                       help="Exit non-zero if any finding meets this severity (default: critical)")

    def _cmd_security(args):
        sub = getattr(args, "security_command", None)
        from talaria_cli.security_audit import cmd_security_audit
        if sub in (None, "audit"):
            return cmd_security_audit(args)
        security_parser.print_help()
        return 2

    audit_parser.set_defaults(func=_cmd_security)
    security_parser.set_defaults(func=_cmd_security)

    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``talaria -c Pokemon Agent Dev`` → ``talaria -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
    from talaria_cli.config import get_container_exec_info
    from talaria_cli.container_exec import _exec_in_container

    container_info = get_container_exec_info()
    if container_info:
        _exec_in_container(container_info, sys.argv[1:])
        # Unreachable: os.execvp never returns on success (process is replaced)
        # and raises OSError on failure (which propagates as a traceback).
        sys.exit(1)

    _processed_argv = _coalesce_session_name_args(sys.argv[1:])

    # ── Defensive subparser routing (bpo-9338 workaround) ───────────
    # On some Python versions (notably <3.11), argparse fails to route
    # subcommand tokens when the parent parser has nargs='?' optional
    # arguments (--continue).  The symptom: "unrecognized arguments: model"
    # even though 'model' is a registered subcommand.
    #
    # Fix: when argv contains a token matching a known subcommand, set
    # subparsers.required=True to force deterministic routing.  If that
    # fails (e.g. 'talaria -c model' where 'model' is consumed as the
    # session name for --continue), fall back to the default behaviour.
    import io as _io

    _known_cmds = (
        set(subparsers.choices.keys()) if hasattr(subparsers, "choices") else set()
    )
    _has_cmd_token = any(
        t in _known_cmds for t in _processed_argv if not t.startswith("-")
    )

    if _has_cmd_token:
        subparsers.required = True
        _saved_stderr = sys.stderr
        try:
            sys.stderr = _io.StringIO()
            args = parser.parse_args(_processed_argv)
            sys.stderr = _saved_stderr
        except SystemExit as exc:
            sys.stderr = _saved_stderr
            # Help/version flags (exit code 0) already printed output —
            # re-raise immediately to avoid a second parse_args printing
            # the same help text again (#10230).
            if exc.code == 0:
                raise
            # Subcommand name was consumed as a flag value (e.g. -c model).
            # Fall back to optional subparsers so argparse handles it normally.
            subparsers.required = False
            args = parser.parse_args(_processed_argv)
    else:
        subparsers.required = False
        args = parser.parse_args(_processed_argv)

    # Handle --version flag
    if args.version:
        cmd_version(args)
        return

    # Discover Python plugins and register shell hooks once, before any
    # command that can fire lifecycle hooks.  Both are idempotent; gated
    # so introspection/management commands (talaria hooks list, cron
    # list, gateway status, mcp add, ...) don't pay discovery cost or
    # trigger consent prompts for hooks the user is still inspecting.
    # Groups with mixed admin/CRUD vs. agent-running entries narrow via
    # the nested subcommand (dest varies by parser).
    _AGENT_COMMANDS = {None, "chat", "acp", "rl"}
    _AGENT_SUBCOMMANDS = {
        "cron":    ("cron_command",    {"run", "tick"}),
        "gateway": ("gateway_command", {"run"}),
        "mcp":     ("mcp_action",      {"serve"}),
    }
    _sub_attr, _sub_set = _AGENT_SUBCOMMANDS.get(args.command, (None, None))
    if (
        args.command in _AGENT_COMMANDS
        or (_sub_attr and getattr(args, _sub_attr, None) in _sub_set)
    ):
        _accept_hooks = bool(getattr(args, "accept_hooks", False))
        try:
            from talaria_cli.plugins import discover_plugins
            discover_plugins()
        except Exception:
            logger.debug(
                "plugin discovery failed at CLI startup", exc_info=True,
            )
        try:
            # MCP tool discovery — no event loop running in CLI/TUI startup,
            # so inline is safe.  Moved here from model_tools.py module scope
            # to avoid freezing the gateway's event loop on its first message
            # via the same lazy import path (#16856).
            from tools.mcp_tool import discover_mcp_tools
            discover_mcp_tools()
        except Exception:
            logger.debug(
                "MCP tool discovery failed at CLI startup", exc_info=True,
            )
        try:
            from agent.shell_hooks import register_from_config
            from talaria_cli.config import load_config
            register_from_config(load_config(), accept_hooks=_accept_hooks)
        except Exception:
            logger.debug(
                "shell-hook registration failed at CLI startup",
                exc_info=True,
            )

    # Handle top-level --oneshot / -z: single-shot mode, stdout = final
    # response only, nothing else. Bypasses cli.py entirely.
    if getattr(args, "oneshot", None):
        from talaria_cli.oneshot import run_oneshot

        sys.exit(run_oneshot(
            args.oneshot,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
        ))

    # Handle top-level --resume / --continue as shortcut to chat
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", False),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Default to chat if no command specified
    if args.command is None:
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", False),
            ("resume", None),
            ("continue_last", None),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Execute the command
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
