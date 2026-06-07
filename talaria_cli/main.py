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

NAVIGATION (file is ~5.2K lines — line numbers approximate)
  L38-235     parser helpers, profile override, time / provider checks
  L350-378    cmd_chat              — interactive (removed) → redirect printer
  L386        cmd_gateway           — gateway subcommands
  L394        cmd_setup             — first-run setup
  L406        cmd_model             — model selection (delegates to provider_flows)
  L442-583    small handlers: logout / auth / status / cron / webhook / slack /
              hooks / doctor / dump / debug / config / backup / import / version / uninstall
  L591-1740   update infrastructure (npm install, fork detect, stash, hangup)
  L1833       cmd_update            — `talaria update`
  L2699       cmd_profile           — profile management
  L2987       cmd_completion        — shell completion
  L3000       cmd_logs              — log viewer
  L3021       def main() — argparse build + dispatch

Modules pulled out for clarity:
  talaria_cli/provider_flows.py  — select_provider_and_model + all
                                    _model_flow_* / _aux_* / OAuth helpers
  talaria_cli/sessions.py        — _session_browse_picker (curses) and
                                    _coalesce_session_name_args
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Re-exported for backward compatibility with callers that import from
# ``talaria_cli.main`` (talaria_cli/setup.py, talaria_cli/fallback_cmd.py).
from talaria_cli.provider_flows import select_provider_and_model  # noqa: F401
from talaria_cli.sessions import (
    _coalesce_session_name_args,
    _session_browse_picker,
)


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
    BRAND_EMOJI,
    DEFAULT_INSTALL_SCRIPT_URL,
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
import time as _time
from datetime import datetime

from talaria_cli import __release_date__, __version__

logger = logging.getLogger(__name__)


def _relative_time(ts) -> str:
    """Format a timestamp as relative time (e.g., '2h ago', 'yesterday')."""
    if not ts:
        return "?"
    delta = _time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    if delta < 172800:
        return "yesterday"
    if delta < 604800:
        return f"{int(delta / 86400)}d ago"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


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


def _clear_bytecode_cache(root: Path) -> int:
    """Remove all __pycache__ directories under *root*.

    Stale .pyc files can cause ImportError after code updates when Python
    loads a cached bytecode file that references names that no longer exist
    (or don't yet exist) in the updated source.  Clearing them forces Python
    to recompile from the .py source on next import.

    Returns the number of directories removed.
    """
    removed = 0
    for dirpath, dirnames, _ in os.walk(root):
        # Skip venv / node_modules / .git entirely
        dirnames[:] = [
            d
            for d in dirnames
            if d not in ("venv", ".venv", "node_modules", ".git", ".worktrees")
        ]
        if os.path.basename(dirpath) == "__pycache__":
            try:
                shutil.rmtree(dirpath)
                removed += 1
            except OSError:
                pass
            dirnames.clear()  # nothing left to recurse into
    return removed


def _gateway_prompt(prompt_text: str, default: str = "", timeout: float = 300.0) -> str:
    """File-based IPC prompt for gateway mode.

    Writes a prompt marker file so the gateway can forward the question to the
    user, then polls for a response file.  Falls back to *default* on timeout.

    Used by ``talaria update --gateway`` so interactive prompts (stash restore,
    config migration) are forwarded to the messenger instead of being silently
    skipped.
    """
    import json as _json
    import uuid as _uuid

    from talaria_constants import get_talaria_home

    home = get_talaria_home()
    prompt_path = home / ".update_prompt.json"
    response_path = home / ".update_response"

    # Clean any stale response file
    response_path.unlink(missing_ok=True)

    payload = {
        "prompt": prompt_text,
        "default": default,
        "id": str(_uuid.uuid4()),
    }
    tmp = prompt_path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(payload))
    tmp.replace(prompt_path)

    # Poll for response
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if response_path.exists():
            try:
                answer = response_path.read_text().strip()
                response_path.unlink(missing_ok=True)
                prompt_path.unlink(missing_ok=True)
                return answer if answer else default
            except (OSError, ValueError):
                pass
        _time.sleep(0.5)

    # Timeout — clean up and use default
    prompt_path.unlink(missing_ok=True)
    response_path.unlink(missing_ok=True)
    print(f"  (no response after {int(timeout)}s, using default: {default!r})")
    return default


def _run_npm_install_deterministic(
    npm: str,
    cwd: Path,
    *,
    extra_args: tuple[str, ...] = (),
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """Run a deterministic npm install that does not mutate ``package-lock.json``.

    Prefers ``npm ci`` (strict, lockfile-preserving) when a lockfile is present;
    falls back to ``npm install`` only if ``npm ci`` fails (e.g. lockfile out of
    sync on a WIP checkout).  Without this, ``npm install`` on npm ≥ 10 silently
    rewrites committed lockfiles (stripping ``"peer": true`` etc.), which leaves
    the working tree dirty and causes the next ``talaria update`` to stash the
    lockfile — repeatedly.
    """
    lockfile = cwd / "package-lock.json"
    if lockfile.exists():
        ci_cmd = [npm, "ci", *extra_args]
        ci_result = subprocess.run(
            ci_cmd,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            check=False,
        )
        if ci_result.returncode == 0:
            return ci_result
        # Fall through to `npm install` — lockfile may be out of sync on a
        # WIP fork/branch, or `npm ci` may not be available on very old npm.
    install_cmd = [npm, "install", *extra_args]
    return subprocess.run(
        install_cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=False,
    )



def _update_via_zip(args):
    """Update Talaria Agent by downloading a ZIP archive.

    Used on Windows when git file I/O is broken (antivirus, NTFS filter
    drivers causing 'Invalid argument' errors on file creation).
    """
    import tempfile
    import zipfile
    from urllib.request import urlretrieve

    branch = "main"
    zip_url = (
        f"{DEFAULT_REPO_HTTPS_URL}/archive/refs/heads/{branch}.zip"
    )

    print("→ Downloading latest version...")
    try:
        tmp_dir = tempfile.mkdtemp(prefix="talaria-update-")
        zip_path = os.path.join(tmp_dir, f"talaria-agent-{branch}.zip")
        urlretrieve(zip_url, zip_path)

        print("→ Extracting...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Validate paths to prevent zip-slip (path traversal)
            tmp_dir_real = os.path.realpath(tmp_dir)
            for member in zf.infolist():
                member_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                if (
                    not member_path.startswith(tmp_dir_real + os.sep)
                    and member_path != tmp_dir_real
                ):
                    raise ValueError(
                        f"Zip-slip detected: {member.filename} escapes extraction directory"
                    )
            zf.extractall(tmp_dir)

        # GitHub ZIPs extract to talaria-agent-<branch>/
        extracted = os.path.join(tmp_dir, f"talaria-agent-{branch}")
        if not os.path.isdir(extracted):
            # Try to find it
            for d in os.listdir(tmp_dir):
                candidate = os.path.join(tmp_dir, d)
                if os.path.isdir(candidate) and d != "__MACOSX":
                    extracted = candidate
                    break

        # Copy updated files over existing installation, preserving venv/node_modules/.git
        preserve = {"venv", "node_modules", ".git", ".env"}
        update_count = 0
        for item in os.listdir(extracted):
            if item in preserve:
                continue
            src = os.path.join(extracted, item)
            dst = os.path.join(str(PROJECT_ROOT), item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            update_count += 1

        print(f"✓ Updated {update_count} items from ZIP")

        # Cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as e:
        print(f"✗ ZIP update failed: {e}")
        sys.exit(1)

    # Clear stale bytecode after ZIP extraction
    removed = _clear_bytecode_cache(PROJECT_ROOT)
    if removed:
        print(
            f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
        )

    # Reinstall Python dependencies. Prefer .[all], but if one optional extra
    # breaks on this machine, keep base deps and reinstall the remaining extras
    # individually so update does not silently strip working capabilities.
    print("→ Updating Python dependencies...")

    uv_bin = shutil.which("uv")
    if uv_bin:
        uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
        _install_python_dependencies_with_optional_fallback([uv_bin, "pip"], env=uv_env)
    else:
        # Use sys.executable to explicitly call the venv's pip module,
        # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
        # Some environments lose pip inside the venv; bootstrap it back with
        # ensurepip before trying the editable install.
        pip_cmd = [sys.executable, "-m", "pip"]
        try:
            subprocess.run(
                pip_cmd + ["--version"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                cwd=PROJECT_ROOT,
                check=True,
            )
        _install_python_dependencies_with_optional_fallback(pip_cmd)

    _update_node_dependencies()

    # Sync skills
    try:
        from tools.skills_sync import sync_skills

        print("→ Syncing bundled skills...")
        result = sync_skills(quiet=True)
        if result["copied"]:
            print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
        if result.get("updated"):
            print(
                f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
            )
        if result.get("user_modified"):
            print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
        if result.get("cleaned"):
            print(f"  − {len(result['cleaned'])} removed from manifest")
        if not result["copied"] and not result.get("updated"):
            print("  ✓ Skills are up to date")
    except Exception:
        pass

    print()
    print("✓ Update complete!")


def _stash_local_changes_if_needed(git_cmd: list[str], cwd: Path) -> Optional[str]:
    status = subprocess.run(
        git_cmd + ["status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        return None

    # If the index has unmerged entries (e.g. from an interrupted merge/rebase),
    # git stash will fail with "needs merge / could not write index".  Clear the
    # conflict state with `git reset` so the stash can proceed.  Working-tree
    # changes are preserved; only the index conflict markers are dropped.
    unmerged = subprocess.run(
        git_cmd + ["ls-files", "--unmerged"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if unmerged.stdout.strip():
        print("→ Clearing unmerged index entries from a previous conflict...")
        subprocess.run(git_cmd + ["reset"], cwd=cwd, capture_output=True)

    from datetime import datetime, timezone

    stash_name = datetime.now(timezone.utc).strftime(
        "talaria-update-autostash-%Y%m%d-%H%M%S"
    )
    print("→ Local changes detected — stashing before update...")
    subprocess.run(
        git_cmd + ["stash", "push", "--include-untracked", "-m", stash_name],
        cwd=cwd,
        check=True,
    )
    stash_ref = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "refs/stash"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return stash_ref


def _resolve_stash_selector(
    git_cmd: list[str], cwd: Path, stash_ref: str
) -> Optional[str]:
    stash_list = subprocess.run(
        git_cmd + ["stash", "list", "--format=%gd %H"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in stash_list.stdout.splitlines():
        selector, _, commit = line.partition(" ")
        if commit.strip() == stash_ref:
            return selector.strip()
    return None


def _print_stash_cleanup_guidance(
    stash_ref: str, stash_selector: Optional[str] = None
) -> None:
    print(
        "  Check `git status` first so you don't accidentally reapply the same change twice."
    )
    print("  Find the saved entry with: git stash list --format='%gd %H %s'")
    if stash_selector:
        print(f"  Remove it with: git stash drop {stash_selector}")
    else:
        print(
            f"  Look for commit {stash_ref}, then drop its selector with: git stash drop stash@{{N}}"
        )


def _restore_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
    prompt_user: bool = False,
    input_fn=None,
) -> bool:
    if prompt_user:
        print()
        print("⚠ Local changes were stashed before updating.")
        print(
            "  Restoring them may reapply local customizations onto the updated codebase."
        )
        print(f"  Review the result afterward if {default_branding('agent_short_name', 'Talaria')} behaves unexpectedly.")
        print("Restore local changes now? [Y/n]")
        if input_fn is not None:
            response = input_fn("Restore local changes now? [Y/n]", "y")
        else:
            response = input().strip().lower()
        if response not in ("", "y", "yes"):
            print("Skipped restoring local changes.")
            print("Your changes are still preserved in git stash.")
            print(f"Restore manually with: git stash apply {stash_ref}")
            return False

    print("→ Restoring local changes...")
    restore = subprocess.run(
        git_cmd + ["stash", "apply", stash_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    # Check for unmerged (conflicted) files — can happen even when returncode is 0
    unmerged = subprocess.run(
        git_cmd + ["diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    has_conflicts = bool(unmerged.stdout.strip())

    if restore.returncode != 0 or has_conflicts:
        print("✗ Update pulled new code, but restoring local changes hit conflicts.")
        if restore.stdout.strip():
            print(restore.stdout.strip())
        if restore.stderr.strip():
            print(restore.stderr.strip())

        # Show which files conflicted
        conflicted_files = unmerged.stdout.strip()
        if conflicted_files:
            print("\nConflicted files:")
            for f in conflicted_files.splitlines():
                print(f"  • {f}")

        print("\nYour stashed changes are preserved — nothing is lost.")
        print(f"  Stash ref: {stash_ref}")

        # Always reset to clean state — leaving conflict markers in source
        # files makes talaria completely unrunnable (SyntaxError on import).
        # The user's changes are safe in the stash for manual recovery.
        subprocess.run(
            git_cmd + ["reset", "--hard", "HEAD"],
            cwd=cwd,
            capture_output=True,
        )
        print("Working tree reset to clean state.")
        print(f"Restore your changes later with: git stash apply {stash_ref}")
        # Don't sys.exit — the code update itself succeeded, only the stash
        # restore had conflicts.  Let cmd_update continue with pip install,
        # skill sync, and gateway restart.
        return False

    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print(
            f"⚠ Local changes were restored, but {default_branding('agent_short_name', 'Talaria')} couldn't find the stash entry to drop."
        )
        print(
            "  The stash was left in place. You can remove it manually after checking the result."
        )
        _print_stash_cleanup_guidance(stash_ref)
    else:
        drop = subprocess.run(
            git_cmd + ["stash", "drop", stash_selector],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if drop.returncode != 0:
            print(
                f"⚠ Local changes were restored, but {default_branding('agent_short_name', 'Talaria')} couldn't drop the saved stash entry."
            )
            if drop.stdout.strip():
                print(drop.stdout.strip())
            if drop.stderr.strip():
                print(drop.stderr.strip())
            print(
                "  The stash was left in place. You can remove it manually after checking the result."
            )
            _print_stash_cleanup_guidance(stash_ref, stash_selector)

    print("⚠ Local changes were restored on top of the updated codebase.")
    print(f"  Review `git diff` / `git status` if {default_branding('agent_short_name', 'Talaria')} behaves unexpectedly.")
    return True


# =========================================================================
# Fork detection and upstream management for `talaria update`
# =========================================================================

from talaria_cli.branding import DEFAULT_REPO_URL as OFFICIAL_REPO_URL


def _derive_repo_url_variants(https_dot_git: str) -> set:
    """Return https/ssh + .git/no-suffix variants of a GitHub URL."""
    base = https_dot_git.removesuffix(".git")  # https://github.com/<owner>/<repo>
    if not base.startswith("https://github.com/"):
        return {https_dot_git}
    path = base[len("https://github.com/"):]  # <owner>/<repo>
    return {
        f"https://github.com/{path}.git",
        f"git@github.com:{path}.git",
        f"https://github.com/{path}",
        f"git@github.com:{path}",
    }


OFFICIAL_REPO_URLS = _derive_repo_url_variants(OFFICIAL_REPO_URL)
SKIP_UPSTREAM_PROMPT_FILE = ".skip_upstream_prompt"


def _get_origin_url(git_cmd: list[str], cwd: Path) -> Optional[str]:
    """Get the URL of the origin remote, or None if not set."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _is_fork(origin_url: Optional[str]) -> bool:
    """Check if the origin remote points to a fork (not the official repo)."""
    if not origin_url:
        return False
    # Normalize URL for comparison (strip trailing .git if present)
    normalized = origin_url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    for official in OFFICIAL_REPO_URLS:
        official_normalized = official.rstrip("/")
        if official_normalized.endswith(".git"):
            official_normalized = official_normalized[:-4]
        if normalized == official_normalized:
            return False
    return True


def _has_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Check if an 'upstream' remote already exists."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "upstream"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _add_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Add the official repo as the 'upstream' remote. Returns True on success."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "add", "upstream", OFFICIAL_REPO_URL],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _count_commits_between(git_cmd: list[str], cwd: Path, base: str, head: str) -> int:
    """Count commits on `head` that are not on `base`. Returns -1 on error."""
    try:
        result = subprocess.run(
            git_cmd + ["rev-list", "--count", f"{base}..{head}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return -1


def _should_skip_upstream_prompt() -> bool:
    """Check if user previously declined to add upstream."""
    from talaria_constants import get_talaria_home

    return (get_talaria_home() / SKIP_UPSTREAM_PROMPT_FILE).exists()


def _mark_skip_upstream_prompt():
    """Create marker file to skip future upstream prompts."""
    try:
        from talaria_constants import get_talaria_home

        (get_talaria_home() / SKIP_UPSTREAM_PROMPT_FILE).touch()
    except Exception:
        pass


def _sync_fork_with_upstream(git_cmd: list[str], cwd: Path) -> bool:
    """Attempt to push updated main to origin (sync fork).

    Returns True if push succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            git_cmd + ["push", "origin", "main", "--force-with-lease"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sync_with_upstream_if_needed(git_cmd: list[str], cwd: Path) -> None:
    """Check if fork is behind upstream and sync if safe.

    This implements the fork upstream sync logic:
    - If upstream remote doesn't exist, ask user if they want to add it
    - Compare origin/main with upstream/main
    - If origin/main is strictly behind upstream/main, pull from upstream
    - Try to sync fork back to origin if possible
    """
    has_upstream = _has_upstream_remote(git_cmd, cwd)

    if not has_upstream:
        # Check if user previously declined
        if _should_skip_upstream_prompt():
            return

        # Ask user if they want to add upstream
        print()
        print("ℹ Your fork is not tracking the official Talaria repository.")
        print("  This means you may miss updates from Jeonhui/Talaria-Agent.")
        print()
        try:
            response = (
                input("Add official repo as 'upstream' remote? [Y/n]: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            response = "n"

        if response in ("", "y", "yes"):
            print("→ Adding upstream remote...")
            if _add_upstream_remote(git_cmd, cwd):
                print(
                    f"  ✓ Added upstream: {OFFICIAL_REPO_URL}"
                )
                has_upstream = True
            else:
                print("  ✗ Failed to add upstream remote. Skipping upstream sync.")
                return
        else:
            print(
                f"  Skipped. Run 'git remote add upstream {OFFICIAL_REPO_URL}' to add later."
            )
            _mark_skip_upstream_prompt()
            return

    # Fetch upstream
    print()
    print("→ Fetching upstream...")
    try:
        subprocess.run(
            git_cmd + ["fetch", "upstream", "--quiet"],
            cwd=cwd,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ✗ Failed to fetch upstream. Skipping upstream sync.")
        return

    # Compare origin/main with upstream/main
    origin_ahead = _count_commits_between(git_cmd, cwd, "upstream/main", "origin/main")
    upstream_ahead = _count_commits_between(
        git_cmd, cwd, "origin/main", "upstream/main"
    )

    if origin_ahead < 0 or upstream_ahead < 0:
        print("  ✗ Could not compare branches. Skipping upstream sync.")
        return

    # If origin/main has commits not on upstream, don't trample
    if origin_ahead > 0:
        print()
        print(f"ℹ Your fork has {origin_ahead} commit(s) not on upstream.")
        print("  Skipping upstream sync to preserve your changes.")
        print("  If you want to merge upstream changes, run:")
        print("    git pull upstream main")
        return

    # If upstream is not ahead, fork is up to date
    if upstream_ahead == 0:
        print("  ✓ Fork is up to date with upstream")
        return

    # origin/main is strictly behind upstream/main (can fast-forward)
    print()
    print(f"→ Fork is {upstream_ahead} commit(s) behind upstream")
    print("→ Pulling from upstream...")

    try:
        subprocess.run(
            git_cmd + ["pull", "--ff-only", "upstream", "main"],
            cwd=cwd,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(
            "  ✗ Failed to pull from upstream. You may need to resolve conflicts manually."
        )
        return

    print("  ✓ Updated from upstream")

    # Try to sync fork back to origin
    print("→ Syncing fork...")
    if _sync_fork_with_upstream(git_cmd, cwd):
        print("  ✓ Fork synced with upstream")
    else:
        print(
            "  ℹ Got updates from upstream but couldn't push to fork (no write access?)"
        )
        print("    Your local repo is updated, but your fork on GitHub may be behind.")


def _invalidate_update_cache():
    """Delete the update-check cache for ALL profiles so no banner
    reports a stale "commits behind" count after a successful update.

    The git repo is shared across profiles — when one profile runs
    ``talaria update``, every profile is now current.
    """
    homes = []
    # Default profile home (Docker-aware — uses /opt/data in Docker)
    from talaria_constants import get_default_talaria_root

    default_home = get_default_talaria_root()
    homes.append(default_home)
    # Named profiles under <root>/profiles/
    profiles_root = default_home / "profiles"
    if profiles_root.is_dir():
        for entry in profiles_root.iterdir():
            if entry.is_dir():
                homes.append(entry)
    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass


def _load_installable_optional_extras() -> list[str]:
    """Return the optional extras referenced by the ``all`` group.

    Only extras that ``[all]`` actually pulls in are retried individually.
    Extras outside ``[all]`` (e.g. ``rl``, ``yc-bench``) are intentionally
    excluded — they have heavy or platform-specific deps that most users
    never installed.
    """
    try:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except Exception:
        return []

    optional_deps = project.get("optional-dependencies", {})
    if not isinstance(optional_deps, dict):
        return []

    # Parse the [all] group to find which extras it references.
    # Entries look like "talaria-agent[matrix]" or "package-name[extra]".
    all_refs = optional_deps.get("all", [])
    referenced: list[str] = []
    for ref in all_refs:
        if "[" in ref and "]" in ref:
            name = ref.split("[", 1)[1].split("]", 1)[0]
            if name in optional_deps:
                referenced.append(name)

    return referenced


def _install_python_dependencies_with_optional_fallback(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Install base deps plus as many optional extras as the environment supports."""
    try:
        subprocess.run(
            install_cmd_prefix + ["install", "-e", ".[all]", "--quiet"],
            cwd=PROJECT_ROOT,
            check=True,
            env=env,
        )
        return
    except subprocess.CalledProcessError:
        print(
            "  ⚠ Optional extras failed, reinstalling base dependencies and retrying extras individually..."
        )

    subprocess.run(
        install_cmd_prefix + ["install", "-e", ".", "--quiet"],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )

    failed_extras: list[str] = []
    installed_extras: list[str] = []
    for extra in _load_installable_optional_extras():
        try:
            subprocess.run(
                install_cmd_prefix + ["install", "-e", f".[{extra}]", "--quiet"],
                cwd=PROJECT_ROOT,
                check=True,
                env=env,
            )
            installed_extras.append(extra)
        except subprocess.CalledProcessError:
            failed_extras.append(extra)

    if installed_extras:
        print(
            f"  ✓ Reinstalled optional extras individually: {', '.join(installed_extras)}"
        )
    if failed_extras:
        print(
            f"  ⚠ Skipped optional extras that still failed: {', '.join(failed_extras)}"
        )


def _update_node_dependencies() -> None:
    npm = shutil.which("npm")
    if not npm:
        return

    paths = (
        ("repo root", PROJECT_ROOT),
        ("ui-tui", PROJECT_ROOT / "ui-tui"),
    )
    if not any((path / "package.json").exists() for _, path in paths):
        return

    print("→ Updating Node.js dependencies...")
    for label, path in paths:
        if not (path / "package.json").exists():
            continue

        result = _run_npm_install_deterministic(
            npm,
            path,
            extra_args=("--silent", "--no-fund", "--no-audit", "--progress=false"),
        )
        if result.returncode == 0:
            print(f"  ✓ {label}")
            continue

        print(f"  ⚠ npm install failed in {label}")
        stderr = (result.stderr or "").strip()
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")


class _UpdateOutputStream:
    """Stream wrapper used during ``talaria update`` to survive terminal loss.

    Wraps the process's original stdout/stderr so that:

    * Every write is also mirrored to an append-only log file
      (``~/.talaria/logs/update.log``) that users can inspect after the
      terminal disconnects.
    * Writes to the original stream that fail with ``BrokenPipeError`` /
      ``OSError`` / ``ValueError`` (closed file) no longer cascade into
      process exit — the update keeps going, only the on-screen output
      stops.

    Combined with ``SIGHUP -> SIG_IGN`` installed by
    ``_install_hangup_protection``, this makes ``talaria update`` safe to
    run in a plain SSH session that might disconnect mid-install.
    """

    def __init__(self, original, log_file):
        self._original = original
        self._log = log_file
        self._original_broken = False

    def write(self, data):
        # Mirror to the log file first — it's the most reliable destination.
        if self._log is not None:
            try:
                self._log.write(data)
            except Exception:
                # Log errors should never abort the update.
                pass

        if self._original_broken:
            return len(data) if isinstance(data, (str, bytes)) else 0

        try:
            return self._original.write(data)
        except (BrokenPipeError, OSError, ValueError):
            # Terminal vanished (SSH disconnect, shell close).  Stop trying
            # to write to it, but keep the update running.
            self._original_broken = True
            return len(data) if isinstance(data, (str, bytes)) else 0

    def flush(self):
        if self._log is not None:
            try:
                self._log.flush()
            except Exception:
                pass
        if self._original_broken:
            return
        try:
            self._original.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._original_broken = True

    def isatty(self):
        if self._original_broken:
            return False
        try:
            return self._original.isatty()
        except Exception:
            return False

    def fileno(self):
        # Some tools probe fileno(); defer to the underlying stream and let
        # callers handle failures (same behaviour as the unwrapped stream).
        return self._original.fileno()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_hangup_protection(gateway_mode: bool = False):
    """Protect ``cmd_update`` from SIGHUP and broken terminal pipes.

    Users commonly run ``talaria update`` in an SSH session or a terminal
    that may close mid-install.  Without protection, ``SIGHUP`` from the
    terminal kills the Python process during ``pip install`` and leaves
    the venv half-installed; the documented workaround ("use screen /
    tmux") shouldn't be required for something as routine as an update.

    Protections installed:

    1. ``SIGHUP`` is set to ``SIG_IGN``.  POSIX preserves ``SIG_IGN``
       across ``exec()``, so pip and git subprocesses also stop dying on
       hangup.
    2. ``sys.stdout`` / ``sys.stderr`` are wrapped to mirror output to
       ``~/.talaria/logs/update.log`` and to silently absorb
       ``BrokenPipeError`` when the terminal vanishes.

    ``SIGINT`` (Ctrl-C) and ``SIGTERM`` (systemd shutdown) are
    **intentionally left alone** — those are legitimate cancellation
    signals the user or OS sent on purpose.

    In gateway mode (``talaria update --gateway``) the update is already
    spawned detached from a terminal, so this function is a no-op.

    Returns a dict that ``cmd_update`` can pass to
    ``_finalize_update_output`` on exit.  Returning a dict rather than a
    tuple keeps the call site forward-compatible with future additions.
    """
    state = {
        "prev_stdout": sys.stdout,
        "prev_stderr": sys.stderr,
        "log_file": None,
        "installed": False,
    }

    if gateway_mode:
        return state

    import signal as _signal

    # (1) Ignore SIGHUP for the remainder of this process.
    if hasattr(_signal, "SIGHUP"):
        try:
            _signal.signal(_signal.SIGHUP, _signal.SIG_IGN)
        except (ValueError, OSError):
            # Called from a non-main thread — not fatal.  The update still
            # runs, just without hangup protection.
            pass

    # (2) Mirror output to update.log and wrap stdio for broken-pipe
    # tolerance.  Any failure here is non-fatal; we just skip the wrap.
    try:
        # Late-bound import so tests can monkeypatch
        # talaria_cli.config.get_talaria_home to simulate setup failure.
        from talaria_cli.config import get_talaria_home as _get_talaria_home

        logs_dir = _get_talaria_home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "update.log"
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")

        import datetime as _dt

        log_file.write(
            f"\n=== talaria update started "
            f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
        )

        state["log_file"] = log_file
        sys.stdout = _UpdateOutputStream(state["prev_stdout"], log_file)
        sys.stderr = _UpdateOutputStream(state["prev_stderr"], log_file)
        state["installed"] = True
    except Exception:
        # Leave stdio untouched on any setup failure.  Update continues
        # without mirroring.
        state["log_file"] = None

    return state


def _finalize_update_output(state):
    """Restore stdio and close the update.log handle opened by ``_install_hangup_protection``."""
    if not state:
        return
    if state.get("installed"):
        try:
            sys.stdout = state.get("prev_stdout", sys.stdout)
        except Exception:
            pass
        try:
            sys.stderr = state.get("prev_stderr", sys.stderr)
        except Exception:
            pass
    log_file = state.get("log_file")
    if log_file is not None:
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass


def _cmd_update_check():
    """Implement ``talaria update --check``: fetch and report without installing."""
    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        print("✗ Not a git repository — cannot check for updates.")
        sys.exit(1)

    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    print("→ Fetching from origin...")
    fetch_result = subprocess.run(
        git_cmd + ["fetch", "origin"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if fetch_result.returncode != 0:
        stderr = fetch_result.stderr.strip()
        if "Could not resolve host" in stderr or "unable to access" in stderr:
            print("✗ Network error — cannot reach the remote repository.")
        elif "Authentication failed" in stderr or "could not read Username" in stderr:
            print("✗ Authentication failed — check your git credentials or SSH key.")
        else:
            print("✗ Failed to fetch from origin.")
            if stderr:
                print(f"  {stderr.splitlines()[0]}")
        sys.exit(1)

    rev_result = subprocess.run(
        git_cmd + ["rev-list", "HEAD..origin/main", "--count"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    behind = int(rev_result.stdout.strip())

    if behind == 0:
        print("✓ Already up to date.")
    else:
        commits_word = "commit" if behind == 1 else "commits"
        print(f"{BRAND_EMOJI} Update available: {behind} {commits_word} behind origin/main.")
        from talaria_cli.config import recommended_update_command
        print(f"  Run '{recommended_update_command()}' to install.")


def _ensure_fhs_path_guard() -> None:
    """Ensure /usr/local/bin is on PATH for RHEL-family root non-login shells.

    Mirrors the post-symlink probe added to ``scripts/install.sh`` so that
    existing FHS-layout root installs on RHEL/CentOS/Rocky/Alma 8+ get
    repaired on ``talaria update`` without requiring a reinstall.  The
    installer's assumption that ``/usr/local/bin`` is on PATH for every
    standard shell breaks on those distros in non-login interactive shells
    (su, sudo -s, tmux panes, some web terminals): /etc/bashrc doesn't
    add /usr/local/bin and /root/.bash_profile doesn't either.  Symptom:
    ``talaria`` prints ``command not found`` even though the symlink lives
    at /usr/local/bin/talaria.

    Silent no-op on: non-Linux, non-root, non-FHS installs, and any system
    where ``bash -i -c 'command -v talaria'`` already resolves.  Idempotent.
    """
    if sys.platform != "linux":
        return
    try:
        if os.geteuid() != 0:
            return
    except AttributeError:
        return
    # Only act when this is actually an FHS-layout install (command link at
    # /usr/local/bin/talaria, code at /usr/local/lib/talaria-agent).
    fhs_link = Path("/usr/local/bin/talaria")
    if not fhs_link.is_symlink() and not fhs_link.exists():
        return

    # Probe a fresh non-login interactive bash the way the user will use it.
    # ``bash -i -c`` sources ~/.bashrc but NOT ~/.bash_profile or /etc/profile,
    # which is the exact scenario where RHEL root loses /usr/local/bin.
    home = os.environ.get("HOME") or "/root"
    try:
        probe = subprocess.run(
            ["env", "-i",
             f"HOME={home}",
             f"TERM={os.environ.get('TERM', 'dumb')}",
             "bash", "-i", "-c", "command -v talaria"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # no bash or probe hung — don't block update on this
    if probe.returncode == 0:
        return  # already on PATH, nothing to do

    path_line = 'export PATH="/usr/local/bin:$PATH"'
    path_comment = (
        "# Talaria Agent — ensure /usr/local/bin is on PATH "
        "(RHEL non-login shells)"
    )
    wrote_any = False
    for candidate in (".bashrc", ".bash_profile"):
        cfg = Path(home) / candidate
        if not cfg.is_file():
            continue
        try:
            existing = cfg.read_text(errors="replace")
        except OSError:
            continue
        # Idempotency: skip if any uncommented PATH= line already references
        # /usr/local/bin.  Mirrors the grep pattern used by install.sh.
        already_guarded = any(
            "/usr/local/bin" in line
            and "PATH" in line
            and not line.lstrip().startswith("#")
            for line in existing.splitlines()
        )
        if already_guarded:
            continue
        try:
            with cfg.open("a", encoding="utf-8") as f:
                f.write("\n" + path_comment + "\n" + path_line + "\n")
        except OSError as e:
            print(f"  ⚠ Could not update {cfg}: {e}")
            continue
        print(f"  ✓ Added /usr/local/bin to PATH in {cfg}")
        wrote_any = True
    if wrote_any:
        print("    (reload your shell or run 'source ~/.bashrc' to pick it up)")


def _run_pre_update_backup(args) -> None:
    """Create a full zip backup of TALARIA_HOME before running the update.

    Gated on ``updates.pre_update_backup`` in config (default false).  Off
    by default because the zip can add minutes to every update on large
    TALARIA_HOME directories.  The ``--backup`` flag on ``talaria update``
    opts in for a single run; ``--no-backup`` forces it off when config
    has it enabled.  Never raises — a backup failure should not block the
    update itself.
    """
    # CLI flags win over config.  --no-backup beats --backup if both are set.
    if getattr(args, "no_backup", False):
        print("◆ Pre-update backup: skipped (--no-backup)")
        print()
        return

    force_backup = bool(getattr(args, "backup", False))

    try:
        from talaria_cli.config import load_config
        cfg = load_config()
    except Exception as exc:
        logging.getLogger(__name__).debug("Could not load config for pre-update backup: %s", exc)
        cfg = {}

    updates_cfg = cfg.get("updates", {}) if isinstance(cfg, dict) else {}
    enabled = updates_cfg.get("pre_update_backup", False)
    keep = updates_cfg.get("backup_keep", 5)

    if not enabled and not force_backup:
        # Silent by default — the backup is off, most users don't need to
        # hear about it on every update.  They can opt in via --backup
        # or by flipping the config knob.
        return

    try:
        from talaria_cli.backup import create_pre_update_backup
    except Exception as exc:
        print(f"⚠ Pre-update backup: could not load backup module ({exc}); continuing update.")
        print()
        return

    print("◆ Creating pre-update backup...")
    t0 = _time.monotonic()
    try:
        out_path = create_pre_update_backup(keep=int(keep))
    except Exception as exc:  # defensive — helper already swallows, but just in case
        print(f"  ⚠ Backup failed: {exc}")
        print("  Continuing with update.")
        print()
        return

    elapsed = _time.monotonic() - t0

    if out_path is None:
        print("  ⚠ Backup skipped (no files found or write failed); continuing update.")
        print()
        return

    try:
        size_bytes = out_path.stat().st_size
    except OSError:
        size_bytes = 0

    # Human-readable size
    size_str = f"{size_bytes} B"
    for unit in ("KB", "MB", "GB"):
        if size_bytes < 1024:
            break
        size_bytes /= 1024
        size_str = f"{size_bytes:.1f} {unit}"

    # Render path using display_talaria_home so the user sees ~/.talaria/...
    try:
        from talaria_constants import display_talaria_home, get_talaria_home
        home = get_talaria_home()
        try:
            display_path = f"{display_talaria_home()}/{out_path.relative_to(home)}"
        except ValueError:
            display_path = str(out_path)
    except Exception:
        display_path = str(out_path)

    print(f"  Saved:    {display_path} ({size_str}, {elapsed:.1f}s)")
    print(f"  Restore:  talaria import {out_path}")
    print("  Disable:  omit --backup (backups are off by default)")
    print("            set updates.pre_update_backup: false in config.yaml")
    print()


def cmd_update(args):
    """Update Talaria Agent to the latest version.

    Thin wrapper around ``_cmd_update_impl``: installs hangup protection,
    runs the update, then restores stdio on the way out (even on
    ``sys.exit`` or unhandled exceptions).
    """
    from talaria_cli.config import is_managed, managed_error

    if is_managed():
        managed_error(f"update {default_branding('agent_name', 'Talaria Agent')}")
        return

    if getattr(args, "check", False):
        _cmd_update_check()
        return

    gateway_mode = getattr(args, "gateway", False)

    # Protect against mid-update terminal disconnects (SIGHUP) and tolerate
    # writes to a closed stdout.  No-op in gateway mode.  See
    # _install_hangup_protection for rationale.
    _update_io_state = _install_hangup_protection(gateway_mode=gateway_mode)
    try:
        _cmd_update_impl(args, gateway_mode=gateway_mode)
    finally:
        _finalize_update_output(_update_io_state)


def _cmd_update_impl(args, gateway_mode: bool):
    """Body of ``cmd_update`` — kept separate so the wrapper can always
    restore stdio even on ``sys.exit``."""
    # In gateway mode, use file-based IPC for prompts instead of stdin
    gw_input_fn = (
        (lambda prompt, default="": _gateway_prompt(prompt, default))
        if gateway_mode
        else None
    )

    print(f"{BRAND_EMOJI} Updating {default_branding('agent_name', 'Talaria Agent')}...")
    print()

    # Pre-update backup — runs before any git/file mutation so users can
    # always roll back to the exact state they had before this update.
    _run_pre_update_backup(args)

    # Try git-based update first, fall back to ZIP download on Windows
    # when git file I/O is broken (antivirus, NTFS filter drivers, etc.)
    use_zip_update = False
    git_dir = PROJECT_ROOT / ".git"

    if not git_dir.exists():
        if sys.platform == "win32":
            use_zip_update = True
        else:
            print("✗ Not a git repository. Please reinstall:")
            print(
                f"  curl -fsSL {DEFAULT_INSTALL_SCRIPT_URL} | bash"
            )
            sys.exit(1)

    # On Windows, git can fail with "unable to write loose object file: Invalid argument"
    # due to filesystem atomicity issues. Set the recommended workaround.
    if sys.platform == "win32" and git_dir.exists():
        subprocess.run(
            [
                "git",
                "-c",
                "windows.appendAtomically=false",
                "config",
                "windows.appendAtomically",
                "false",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )

    # Build git command once — reused for fork detection and the update itself.
    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Detect if we're updating from a fork (before any branch logic)
    origin_url = _get_origin_url(git_cmd, PROJECT_ROOT)
    is_fork = _is_fork(origin_url)

    if is_fork:
        print("⚠ Updating from fork:")
        print(f"  {origin_url}")
        print()

    if use_zip_update:
        # ZIP-based update for Windows when git is broken
        _update_via_zip(args)
        return

    # Fetch and pull
    try:

        print("→ Fetching updates...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch", "origin"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.strip()
            if "Could not resolve host" in stderr or "unable to access" in stderr:
                print("✗ Network error — cannot reach the remote repository.")
                print(f"  {stderr.splitlines()[0]}" if stderr else "")
            elif (
                "Authentication failed" in stderr or "could not read Username" in stderr
            ):
                print(
                    "✗ Authentication failed — check your git credentials or SSH key."
                )
            else:
                print("✗ Failed to fetch updates from origin.")
                if stderr:
                    print(f"  {stderr.splitlines()[0]}")
            sys.exit(1)

        # Get current branch (returns literal "HEAD" when detached)
        result = subprocess.run(
            git_cmd + ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        current_branch = result.stdout.strip()

        # Always update against main
        branch = "main"

        # If user is on a non-main branch or detached HEAD, switch to main
        if current_branch != "main":
            label = (
                "detached HEAD"
                if current_branch == "HEAD"
                else f"branch '{current_branch}'"
            )
            print(f"  ⚠ Currently on {label} — switching to main for update...")
            # Stash before checkout so uncommitted work isn't lost
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)
            subprocess.run(
                git_cmd + ["checkout", "main"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)

        prompt_for_restore = auto_stash_ref is not None and (
            gateway_mode or (sys.stdin.isatty() and sys.stdout.isatty())
        )

        # Check if there are updates
        result = subprocess.run(
            git_cmd + ["rev-list", f"HEAD..origin/{branch}", "--count"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_count = int(result.stdout.strip())

        if commit_count == 0:
            _invalidate_update_cache()
            # Restore stash and switch back to original branch if we moved
            if auto_stash_ref is not None:
                _restore_stashed_changes(
                    git_cmd,
                    PROJECT_ROOT,
                    auto_stash_ref,
                    prompt_user=prompt_for_restore,
                    input_fn=gw_input_fn,
                )
            if current_branch not in ("main", "HEAD"):
                subprocess.run(
                    git_cmd + ["checkout", current_branch],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            print("✓ Already up to date!")
            return

        print(f"→ Found {commit_count} new commit(s)")

        # Snapshot critical state (state.db, config, pairing JSONs, etc.)
        # before pulling so a user can recover if something goes wrong.
        # Issue #15733 reported missing pairing data after an update; even
        # though `git pull` can't touch $TALARIA_HOME, this is cheap
        # belt-and-suspenders insurance and gives the user something to
        # restore from via `/snapshot list` / `/snapshot restore <id>`.
        try:
            from talaria_cli.backup import create_quick_snapshot

            snap_id = create_quick_snapshot(label="pre-update")
            if snap_id:
                print(f"  ✓ Pre-update snapshot: {snap_id}")
        except Exception as exc:
            # Never let a snapshot failure block an update.
            logger.debug("Pre-update snapshot failed: %s", exc)

        print("→ Pulling updates...")
        update_succeeded = False
        try:
            pull_result = subprocess.run(
                git_cmd + ["pull", "--ff-only", "origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if pull_result.returncode != 0:
                # ff-only failed — local and remote have diverged (e.g. upstream
                # force-pushed or rebase).  Since local changes are already
                # stashed, reset to match the remote exactly.
                print(
                    "  ⚠ Fast-forward not possible (history diverged), resetting to match remote..."
                )
                reset_result = subprocess.run(
                    git_cmd + ["reset", "--hard", f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if reset_result.returncode != 0:
                    print(f"✗ Failed to reset to origin/{branch}.")
                    if reset_result.stderr.strip():
                        print(f"  {reset_result.stderr.strip()}")
                    print(
                        "  Try manually: git fetch origin && git reset --hard origin/main"
                    )
                    sys.exit(1)
            update_succeeded = True
        finally:
            if auto_stash_ref is not None:
                # Don't attempt stash restore if the code update itself failed —
                # working tree is in an unknown state.
                if not update_succeeded:
                    print(
                        f"  ℹ️  Local changes preserved in stash (ref: {auto_stash_ref})"
                    )
                    print("  Restore manually with: git stash apply")
                else:
                    _restore_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                        prompt_user=prompt_for_restore,
                        input_fn=gw_input_fn,
                    )

        _invalidate_update_cache()

        # Clear stale .pyc bytecode cache — prevents ImportError on gateway
        # restart when updated source references names that didn't exist in
        # the old bytecode (e.g. get_talaria_home added to talaria_constants).
        removed = _clear_bytecode_cache(PROJECT_ROOT)
        if removed:
            print(
                f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}"
            )

        # Fork upstream sync logic (only for main branch on forks)
        if is_fork and branch == "main":
            _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)

        # Reinstall Python dependencies. Prefer .[all], but if one optional extra
        # breaks on this machine, keep base deps and reinstall the remaining extras
        # individually so update does not silently strip working capabilities.
        print("→ Updating Python dependencies...")
        uv_bin = shutil.which("uv")
        if uv_bin:
            uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
            _install_python_dependencies_with_optional_fallback(
                [uv_bin, "pip"], env=uv_env
            )
        else:
            # Use sys.executable to explicitly call the venv's pip module,
            # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
            # Some environments lose pip inside the venv; bootstrap it back with
            # ensurepip before trying the editable install.
            pip_cmd = [sys.executable, "-m", "pip"]
            try:
                subprocess.run(
                    pip_cmd + ["--version"],
                    cwd=PROJECT_ROOT,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    check=True,
                )
            _install_python_dependencies_with_optional_fallback(pip_cmd)

        _update_node_dependencies()

        print()
        print("✓ Code updated!")

        # After git pull, source files on disk are newer than cached Python
        # modules in this process.  Reload talaria_constants so that any lazy
        # import executed below (skills sync, gateway restart) sees new
        # attributes like display_talaria_home() added since the last release.
        try:
            import importlib

            import talaria_constants as _hc

            importlib.reload(_hc)
        except Exception:
            pass  # non-fatal — worst case a lazy import fails gracefully

        # Sync bundled skills (copies new, updates changed, respects user deletions)
        try:
            from tools.skills_sync import sync_skills

            print()
            print("→ Syncing bundled skills...")
            result = sync_skills(quiet=True)
            if result["copied"]:
                print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
            if result.get("updated"):
                print(
                    f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}"
                )
            if result.get("user_modified"):
                print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
            if result.get("cleaned"):
                print(f"  − {len(result['cleaned'])} removed from manifest")
            if not result["copied"] and not result.get("updated"):
                print("  ✓ Skills are up to date")
        except Exception as e:
            logger.debug("Skills sync during update failed: %s", e)

        # Sync bundled skills to all other profiles
        try:
            from talaria_cli.profiles import (
                get_active_profile_name,
                list_profiles,
                seed_profile_skills,
            )

            active = get_active_profile_name()
            other_profiles = [p for p in list_profiles() if p.name != active]
            if other_profiles:
                print()
                print("→ Syncing bundled skills to other profiles...")
                for p in other_profiles:
                    try:
                        r = seed_profile_skills(p.path, quiet=True)
                        if r:
                            copied = len(r.get("copied", []))
                            updated = len(r.get("updated", []))
                            modified = len(r.get("user_modified", []))
                            parts = []
                            if copied:
                                parts.append(f"+{copied} new")
                            if updated:
                                parts.append(f"↑{updated} updated")
                            if modified:
                                parts.append(f"~{modified} user-modified")
                            status = ", ".join(parts) if parts else "up to date"
                        else:
                            status = "sync failed"
                        print(f"  {p.name}: {status}")
                    except Exception as pe:
                        print(f"  {p.name}: error ({pe})")
        except Exception:
            pass  # profiles module not available or no profiles

        # Check for config migrations
        print()
        print("→ Checking configuration for new options...")

        from talaria_cli.config import (
            check_config_version,
            get_missing_config_fields,
            get_missing_env_vars,
            migrate_config,
        )

        missing_env = get_missing_env_vars(required_only=True)
        missing_config = get_missing_config_fields()
        current_ver, latest_ver = check_config_version()

        needs_migration = missing_env or missing_config or current_ver < latest_ver

        if needs_migration:
            print()
            if missing_env:
                print(
                    f"  ⚠️  {len(missing_env)} new required setting(s) need configuration"
                )
            if missing_config:
                print(f"  ℹ️  {len(missing_config)} new config option(s) available")

            print()
            if gateway_mode:
                response = (
                    _gateway_prompt(
                        "Would you like to configure new options now? [Y/n]", "n"
                    )
                    .strip()
                    .lower()
                )
            elif not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("  ℹ Non-interactive session — skipping config migration prompt.")
                print(
                    "    Run 'talaria config migrate' later to apply any new config/env options."
                )
                response = "n"
            else:
                try:
                    response = (
                        input("Would you like to configure them now? [Y/n]: ")
                        .strip()
                        .lower()
                    )
                except EOFError:
                    response = "n"

            if response in ("", "y", "yes"):
                print()
                # In gateway mode, run auto-migrations only (no input() prompts
                # for API keys which would hang the detached process).
                results = migrate_config(interactive=not gateway_mode, quiet=False)

                if results["env_added"] or results["config_added"]:
                    print()
                    print("✓ Configuration updated!")
                if gateway_mode and missing_env:
                    print("  ℹ API keys require manual entry: talaria config migrate")
            else:
                print()
                print("Skipped. Run 'talaria config migrate' later to configure.")
        else:
            print("  ✓ Configuration is up to date")

        print()
        print("✓ Update complete!")

        # Repair RHEL-family root installs where /usr/local/bin isn't on PATH
        # for non-login interactive shells.  No-op on every other platform.
        try:
            _ensure_fhs_path_guard()
        except Exception as e:
            logger.debug("FHS PATH guard check failed: %s", e)

        # Write exit code *before* the gateway restart attempt.
        # When running as ``talaria update --gateway`` (spawned by the gateway's
        # /update command), this process lives inside the gateway's systemd
        # cgroup.  A graceful SIGUSR1 restart keeps the drain loop alive long
        # enough for the exit-code marker to be written below, but the
        # fallback ``systemctl restart`` path (see below) kills everything in
        # the cgroup (KillMode=mixed → SIGKILL to remaining processes),
        # including us and the wrapping bash shell.  The shell never reaches
        # its ``printf $status > .update_exit_code`` epilogue, so the
        # exit-code marker file would never be created.  The new gateway's
        # update watcher would then poll for 30 minutes and send a spurious
        # timeout message.
        #
        # Writing the marker here — after git pull + pip install succeed but
        # before we attempt the restart — ensures the new gateway sees it
        # regardless of how we die.
        if gateway_mode:
            _exit_code_path = get_talaria_home() / ".update_exit_code"
            try:
                _exit_code_path.write_text("0")
            except OSError:
                pass

        # Auto-restart ALL gateways after update.
        # The code update (git pull) is shared across all profiles, so every
        # running gateway needs restarting to pick up the new code.
        try:
            import signal as _signal

            from talaria_cli.gateway import (
                _ensure_user_systemd_env,
                _get_service_pids,
                _graceful_restart_via_sigusr1,
                find_gateway_pids,
                is_macos,
                supports_systemd_services,
            )

            def _wait_for_service_active(
                scope_cmd_: list, svc_name_: str, timeout: float = 10.0,
            ) -> bool:
                """Poll ``systemctl is-active`` until the unit reports active.

                systemd's Stopped -> Started transition after a graceful exit
                (or a hard restart) is not instantaneous; a one-shot check
                races that window and falsely reports the unit as down.
                Poll every 0.5s up to ``timeout`` seconds before giving up.
                """
                deadline = _time.monotonic() + max(timeout, 0.5)
                while True:
                    try:
                        _verify = subprocess.run(
                            scope_cmd_ + ["is-active", svc_name_],
                            capture_output=True, text=True, timeout=5,
                        )
                        if _verify.stdout.strip() == "active":
                            return True
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass
                    if _time.monotonic() >= deadline:
                        return False
                    _time.sleep(0.5)

            def _service_restart_sec(
                scope_cmd_: list, svc_name_: str, default: float = 0.0,
            ) -> float:
                """Read the unit's ``RestartUSec`` (RestartSec) in seconds.

                After a graceful exit-75, systemd waits ``RestartSec`` before
                respawning the unit.  Callers that poll for ``is-active``
                must use a timeout >= ``RestartSec`` + transition slack, or
                they'll give up *during* the cooldown window and wrongly
                conclude the unit didn't relaunch.
                """
                try:
                    _show = subprocess.run(
                        scope_cmd_ + [
                            "show", svc_name_,
                            "--property=RestartUSec", "--value",
                        ],
                        capture_output=True, text=True, timeout=5,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    return default
                raw = (_show.stdout or "").strip()
                # systemd emits values like "30s", "100ms", "1min 30s", or
                # "infinity".  Parse conservatively; on any miss return default.
                if not raw or raw == "infinity":
                    return default
                total = 0.0
                matched = False
                for part in raw.split():
                    for _suf, _mult in (
                        ("ms", 0.001),
                        ("us", 0.000001),
                        ("min", 60.0),
                        ("s", 1.0),
                    ):
                        if part.endswith(_suf):
                            try:
                                total += float(part[: -len(_suf)]) * _mult
                                matched = True
                            except ValueError:
                                pass
                            break
                return total if matched else default

            # Drain budget for graceful SIGUSR1 restarts.  The gateway drains
            # for up to ``agent.restart_drain_timeout`` (default 60s) before
            # exiting with code 75; we wait slightly longer so the drain
            # completes before we fall back to a hard restart.  On older
            # systemd units without SIGUSR1 wiring this wait just times out
            # and we fall back to ``systemctl restart`` (the old behaviour).
            try:
                from gateway.restart import (
                    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT as _DEFAULT_DRAIN,
                )
            except Exception:
                _DEFAULT_DRAIN = 60.0
            _cfg_drain = None
            try:
                from talaria_cli.config import load_config
                _cfg_agent = (load_config().get("agent") or {})
                _cfg_drain = _cfg_agent.get("restart_drain_timeout")
            except Exception:
                pass
            try:
                _drain_budget = float(_cfg_drain) if _cfg_drain is not None else float(_DEFAULT_DRAIN)
            except (TypeError, ValueError):
                _drain_budget = float(_DEFAULT_DRAIN)
            # Add a 15s margin so the drain loop + final exit finish before
            # we escalate to ``systemctl restart`` / SIGTERM.
            _drain_budget = max(_drain_budget, 30.0) + 15.0

            restarted_services = []
            killed_pids = set()

            # --- Systemd services (Linux) ---
            # Discover all talaria-gateway* units (default + profiles)
            if supports_systemd_services():
                try:
                    _ensure_user_systemd_env()
                except Exception:
                    pass

                for scope, scope_cmd in [
                    ("user", ["systemctl", "--user"]),
                    ("system", ["systemctl"]),
                ]:
                    try:
                        result = subprocess.run(
                            scope_cmd
                            + [
                                "list-units",
                                "talaria-gateway*",
                                "--plain",
                                "--no-legend",
                                "--no-pager",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        for line in result.stdout.strip().splitlines():
                            parts = line.split()
                            if not parts:
                                continue
                            unit = parts[
                                0
                            ]  # e.g. talaria-gateway.service or talaria-gateway-coder.service
                            if not unit.endswith(".service"):
                                continue
                            svc_name = unit.removesuffix(".service")
                            # Check if active
                            check = subprocess.run(
                                scope_cmd + ["is-active", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if check.stdout.strip() != "active":
                                continue

                            # Prefer a graceful SIGUSR1 restart so in-flight
                            # agent runs drain instead of being SIGKILLed.
                            # The gateway's SIGUSR1 handler calls
                            # request_restart(via_service=True) → drain →
                            # exit(75); systemd's Restart=on-failure (and
                            # RestartForceExitStatus=75) respawns the unit.
                            _main_pid = 0
                            try:
                                _show = subprocess.run(
                                    scope_cmd + [
                                        "show", svc_name,
                                        "--property=MainPID", "--value",
                                    ],
                                    capture_output=True, text=True, timeout=5,
                                )
                                _main_pid = int((_show.stdout or "").strip() or 0)
                            except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
                                _main_pid = 0

                            _graceful_ok = False
                            if _main_pid > 0:
                                print(
                                    f"  → {svc_name}: draining (up to {int(_drain_budget)}s)..."
                                )
                                _graceful_ok = _graceful_restart_via_sigusr1(
                                    _main_pid, drain_timeout=_drain_budget,
                                )

                            if _graceful_ok:
                                # Gateway exited 75; systemd should relaunch
                                # via Restart=on-failure.  The unit's
                                # RestartSec (default 30s on ours) gates the
                                # respawn — poll past that + slack so we
                                # don't give up mid-cooldown and falsely
                                # print "drained but didn't relaunch".  For
                                # units without RestartSec set we fall back
                                # to the original 10s budget.
                                _restart_sec = _service_restart_sec(
                                    scope_cmd, svc_name, default=0.0,
                                )
                                _post_drain_timeout = max(
                                    10.0, _restart_sec + 10.0,
                                )
                                if _wait_for_service_active(
                                    scope_cmd, svc_name,
                                    timeout=_post_drain_timeout,
                                ):
                                    restarted_services.append(svc_name)
                                    continue
                                # Process exited but wasn't respawned (older
                                # unit without Restart=on-failure or
                                # RestartForceExitStatus=75).  Fall through
                                # to systemctl start/restart.
                                print(
                                    f"  ⚠ {svc_name} drained but didn't relaunch — forcing restart"
                                )

                            # Fallback: blunt systemctl restart.  This is
                            # what the old code always did; we get here only
                            # when the graceful path failed (unit missing
                            # SIGUSR1 wiring, drain exceeded the budget,
                            # restart-policy mismatch).
                            restart = subprocess.run(
                                scope_cmd + ["restart", svc_name],
                                capture_output=True,
                                text=True,
                                timeout=15,
                            )
                            if restart.returncode == 0:
                                # Verify the service actually survived the
                                # restart.  systemctl restart returns 0 even
                                # if the new process crashes immediately.
                                if _wait_for_service_active(
                                    scope_cmd, svc_name, timeout=10.0,
                                ):
                                    restarted_services.append(svc_name)
                                else:
                                    # Retry once — transient startup failures
                                    # (stale module cache, import race) often
                                    # resolve on the second attempt.
                                    print(
                                        f"  ⚠ {svc_name} died after restart, retrying..."
                                    )
                                    subprocess.run(
                                        scope_cmd + ["restart", svc_name],
                                        capture_output=True,
                                        text=True,
                                        timeout=15,
                                    )
                                    if _wait_for_service_active(
                                        scope_cmd, svc_name, timeout=10.0,
                                    ):
                                        restarted_services.append(svc_name)
                                        print(f"  ✓ {svc_name} recovered on retry")
                                    else:
                                        print(
                                            f"  ✗ {svc_name} failed to stay running after restart.\n"
                                            f"    Check logs: journalctl --user -u {svc_name} --since '2 min ago'\n"
                                            f"    Restart manually: systemctl {'--user ' if scope == 'user' else ''}restart {svc_name}"
                                        )
                            else:
                                print(
                                    f"  ⚠ Failed to restart {svc_name}: {restart.stderr.strip()}"
                                )
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass

            # --- Launchd services (macOS) ---
            if is_macos():
                try:
                    from talaria_cli.gateway import (
                        get_launchd_label,
                        get_launchd_plist_path,
                        launchd_restart,
                    )

                    plist_path = get_launchd_plist_path()
                    if plist_path.exists():
                        check = subprocess.run(
                            ["launchctl", "list", get_launchd_label()],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if check.returncode == 0:
                            try:
                                launchd_restart()
                                restarted_services.append(get_launchd_label())
                            except subprocess.CalledProcessError as e:
                                stderr = (getattr(e, "stderr", "") or "").strip()
                                print(f"  ⚠ Gateway restart failed: {stderr}")
                except (FileNotFoundError, subprocess.TimeoutExpired, ImportError):
                    pass

            # --- Manual (non-service) gateways ---
            # Kill any remaining gateway processes not managed by a service.
            # Exclude PIDs that belong to just-restarted services so we don't
            # immediately kill the process that systemd/launchd just spawned.
            service_pids = _get_service_pids()
            manual_pids = find_gateway_pids(
                exclude_pids=service_pids, all_profiles=True
            )
            for pid in manual_pids:
                try:
                    os.kill(pid, _signal.SIGTERM)
                    killed_pids.add(pid)
                except (ProcessLookupError, PermissionError):
                    pass

            if restarted_services or killed_pids:
                print()
                for svc in restarted_services:
                    print(f"  ✓ Restarted {svc}")
                if killed_pids:
                    print(f"  → Stopped {len(killed_pids)} manual gateway process(es)")
                    print("    Restart manually: talaria gateway run")
                    # Also restart for each profile if needed
                    if len(killed_pids) > 1:
                        print(
                            "    (or: talaria -p <profile> gateway run  for each profile)"
                        )

            if not restarted_services and not killed_pids:
                # No gateways were running — nothing to do
                pass

        except Exception as e:
            logger.debug("Gateway restart during update failed: %s", e)

        # Warn if legacy Talaria gateway unit files are still installed.
        # When both talaria.service (from a pre-rename install) and the
        # current talaria-gateway.service are enabled, they SIGTERM-fight
        # for the same bot token (see PR #11909). Flagging here means
        # every `talaria update` surfaces the issue until the user migrates.
        try:
            from talaria_cli.gateway import (
                _find_legacy_talaria_units,
                has_legacy_talaria_units,
                supports_systemd_services,
            )

            if supports_systemd_services() and has_legacy_talaria_units():
                print()
                print(f"⚠ Legacy {default_branding('agent_short_name', 'Talaria')} gateway unit(s) detected:")
                for name, path, is_sys in _find_legacy_talaria_units():
                    scope = "system" if is_sys else "user"
                    print(f"    {path}  ({scope} scope)")
                print()
                print("  These pre-rename units (talaria.service) fight the current")
                print("  talaria-gateway.service for the bot token and cause SIGTERM")
                print("  flap loops. Remove them with:")
                print()
                print("    talaria gateway migrate-legacy")
                print()
                print("  (add `sudo` if any are in system scope)")
        except Exception as e:
            logger.debug("Legacy unit check during update failed: %s", e)

        # Kill stale dashboard processes — the dashboard has no service
        # manager, so leaving it alive after a code update produces a
        # silent frontend/backend mismatch.  We can't auto-restart it
        # (no saved launch args) but we can stop it, and a hint is
        # printed for the user to re-launch.

        print()
        print("Tip: You can now select a provider and model:")
        print("  talaria model              # Select provider and model")

    except subprocess.CalledProcessError as e:
        if sys.platform == "win32":
            print(f"⚠ Git update failed: {e}")
            print("→ Falling back to ZIP download...")
            print()
            _update_via_zip(args)
        else:
            print(f"✗ Update failed: {e}")
            sys.exit(1)


            i += 1
    return result


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
