"""
Doctor command for talaria CLI.

Diagnoses issues with Talaria Agent setup.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from talaria_cli.config import get_env_path, get_project_root, get_talaria_home
from talaria_constants import display_talaria_home

PROJECT_ROOT = get_project_root()
TALARIA_HOME = get_talaria_home()
_DHH = display_talaria_home()  # user-facing display path (e.g. ~/.talaria or ~/.talaria/profiles/coder)

# Load environment variables from ~/.talaria/.env so API key checks work
from dotenv import load_dotenv

_env_path = get_env_path()
if _env_path.exists():
    try:
        load_dotenv(_env_path, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(_env_path, encoding="latin-1")
# Also try project .env as dev fallback
load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")

from talaria_cli.colors import Colors, color
from talaria_cli.models import _TALARIA_USER_AGENT
from talaria_constants import OPENROUTER_MODELS_URL
from utils import base_url_host_matches

_PROVIDER_ENV_HINTS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
    "NOUS_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CN_API_KEY",
    "GMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "HF_TOKEN",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "XIAOMI_API_KEY",
    "TOKENHUB_API_KEY",
)


from talaria_constants import is_termux as _is_termux


def _python_install_cmd() -> str:
    return "python -m pip install" if _is_termux() else "uv pip install"


def _system_package_install_cmd(pkg: str) -> str:
    if _is_termux():
        return f"pkg install {pkg}"
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    return f"sudo apt install {pkg}"


def _safe_which(cmd: str) -> str | None:
    """shutil.which wrapper resilient to platform monkeypatching in tests."""
    try:
        return shutil.which(cmd)
    except Exception:
        return None


def _termux_browser_setup_steps(node_installed: bool) -> list[str]:
    steps: list[str] = []
    step = 1
    if not node_installed:
        steps.append(f"{step}) pkg install nodejs")
        step += 1
    steps.append(f"{step}) npm install -g agent-browser")
    steps.append(f"{step + 1}) agent-browser install")
    return steps


def _has_provider_env_config(content: str) -> bool:
    """Return True when ~/.talaria/.env contains provider auth/base URL settings."""
    return any(key in content for key in _PROVIDER_ENV_HINTS)


def check_ok(text: str, detail: str = ""):
    print(f"  {color('✓', Colors.GREEN)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_warn(text: str, detail: str = ""):
    print(f"  {color('⚠', Colors.YELLOW)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_fail(text: str, detail: str = ""):
    print(f"  {color('✗', Colors.RED)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_info(text: str):
    print(f"    {color('→', Colors.CYAN)} {text}")


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout."""
    try:
        from talaria_cli.gateway import (
            get_systemd_linger_status,
            get_systemd_unit_path,
            is_linux,
        )
    except Exception as e:
        check_warn("Gateway service linger", f"(could not import gateway helpers: {e})")
        return

    if not is_linux():
        return

    unit_path = get_systemd_unit_path()
    if not unit_path.exists():
        return

    print()
    print(color("◆ Gateway Service", Colors.CYAN, Colors.BOLD))

    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is True:
        check_ok("Systemd linger enabled", "(gateway service survives logout)")
    elif linger_enabled is False:
        check_warn("Systemd linger disabled", "(gateway may stop after logout)")
        check_info("Run: sudo loginctl enable-linger $USER")
        issues.append("Enable linger for the gateway user service: sudo loginctl enable-linger $USER")
    else:
        check_warn("Could not verify systemd linger", f"({linger_detail})")


def run_doctor(args):
    """Run diagnostic checks."""
    should_fix = getattr(args, 'fix', False)

    # Doctor runs from the interactive CLI, so CLI-gated tool availability
    # checks (like cronjob management) should see the same context as `talaria`.
    os.environ.setdefault("TALARIA_INTERACTIVE", "1")
    
    issues = []
    manual_issues = []  # issues that can't be auto-fixed
    fixed_count = 0
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                 🩺 Talaria Doctor                        │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    
    # =========================================================================
    # Check: Python version
    # =========================================================================
    print()
    print(color("◆ Python Environment", Colors.CYAN, Colors.BOLD))
    
    py_version = sys.version_info
    if py_version >= (3, 11):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 10):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 8):
        check_warn(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "(3.10+ recommended)")
    else:
        check_fail(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "(3.10+ required)")
        issues.append("Upgrade Python to 3.10+")
    
    # Check if in virtual environment
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        check_ok("Virtual environment active")
    else:
        check_warn("Not in virtual environment", "(recommended)")
    
    # =========================================================================
    # Check: Required packages
    # =========================================================================
    print()
    print(color("◆ Required Packages", Colors.CYAN, Colors.BOLD))
    
    required_packages = [
        ("openai", "OpenAI SDK"),
        ("rich", "Rich (terminal UI)"),
        ("dotenv", "python-dotenv"),
        ("yaml", "PyYAML"),
        ("httpx", "HTTPX"),
    ]
    
    optional_packages = [
        ("croniter", "Croniter (cron expressions)"),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    
    for module, name in required_packages:
        try:
            __import__(module)
            check_ok(name)
        except ImportError:
            check_fail(name, "(missing)")
            issues.append(f"Install {name}: {_python_install_cmd()} {module}")
    
    for module, name in optional_packages:
        try:
            __import__(module)
            check_ok(name, "(optional)")
        except ImportError:
            check_warn(name, "(optional, not installed)")
    
    # =========================================================================
    # Check: Configuration files
    # =========================================================================
    print()
    print(color("◆ Configuration Files", Colors.CYAN, Colors.BOLD))
    
    # Check ~/.talaria/.env (primary location for user config)
    env_path = TALARIA_HOME / '.env'
    if env_path.exists():
        check_ok(f"{_DHH}/.env file exists")
        
        # Check for common issues
        content = env_path.read_text()
        if _has_provider_env_config(content):
            check_ok("API key or custom endpoint configured")
        else:
            check_warn(f"No API key found in {_DHH}/.env")
            issues.append("Run 'talaria setup' to configure API keys")
    else:
        # Also check project root as fallback
        fallback_env = PROJECT_ROOT / '.env'
        if fallback_env.exists():
            check_ok(".env file exists (in project directory)")
        else:
            check_fail(f"{_DHH}/.env file missing")
            if should_fix:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.touch()
                check_ok(f"Created empty {_DHH}/.env")
                check_info("Run 'talaria setup' to configure API keys")
                fixed_count += 1
            else:
                check_info("Run 'talaria setup' to create one")
                issues.append("Run 'talaria setup' to create .env")
    
    # Check ~/.talaria/config.yaml (primary) or project cli-config.yaml (fallback)
    config_path = TALARIA_HOME / 'config.yaml'
    if config_path.exists():
        check_ok(f"{_DHH}/config.yaml exists")

        # Validate model.provider and model.default values
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_section = cfg.get("model") or {}
            provider_raw = (model_section.get("provider") or "").strip()
            provider = provider_raw.lower()
            default_model = (model_section.get("default") or model_section.get("model") or "").strip()

            known_providers: set = set()
            try:
                from talaria_cli.auth import (
                    PROVIDER_REGISTRY,
                )
                from talaria_cli.auth import (
                    resolve_provider as _resolve_auth_provider,
                )
                known_providers = {"anthropic", "openai", "openai-codex", "xiaomi", "lmstudio", "custom", "auto"}
            except Exception:
                _resolve_auth_provider = None
                pass
            try:
                from talaria_cli.config import get_compatible_custom_providers as _compatible_custom_providers
                from talaria_cli.providers import (
                    normalize_provider as _normalize_catalog_provider,
                )
                from talaria_cli.providers import (
                    resolve_provider_full as _resolve_provider_full,
                )
            except Exception:
                _compatible_custom_providers = None
                _normalize_catalog_provider = None
                _resolve_provider_full = None

            custom_providers = []
            if _compatible_custom_providers is not None:
                try:
                    custom_providers = _compatible_custom_providers(cfg)
                except Exception:
                    custom_providers = []

            user_providers = cfg.get("providers")
            if isinstance(user_providers, dict):
                known_providers.update(str(name).strip().lower() for name in user_providers if str(name).strip())
            for entry in custom_providers:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    known_providers.add("custom:" + name.lower().replace(" ", "-"))

            valid_provider_ids = set(known_providers)
            provider_ids_to_accept = {provider} if provider else set()
            if _normalize_catalog_provider is not None:
                for known_provider in known_providers:
                    try:
                        valid_provider_ids.add(_normalize_catalog_provider(known_provider))
                    except Exception:
                        continue

            runtime_provider = provider
            if (
                provider
                and _resolve_auth_provider is not None
                and provider not in ("auto", "custom")
            ):
                try:
                    runtime_provider = _resolve_auth_provider(provider)
                    provider_ids_to_accept.add(runtime_provider)
                except Exception:
                    runtime_provider = provider

            catalog_provider = provider
            if (
                provider
                and _resolve_provider_full is not None
                and provider not in ("auto", "custom")
            ):
                provider_def = _resolve_provider_full(provider, user_providers, custom_providers)
                catalog_provider = provider_def.id if provider_def is not None else None
                if catalog_provider is not None:
                    provider_ids_to_accept.add(catalog_provider)

            if provider and provider != "auto":
                if catalog_provider is None or (
                    known_providers
                    and not (provider_ids_to_accept & valid_provider_ids)
                ):
                    known_list = ", ".join(sorted(known_providers)) if known_providers else "(unavailable)"
                    check_fail(
                        f"model.provider '{provider_raw}' is not a recognised provider",
                        f"(known: {known_list})",
                    )
                    issues.append(
                        f"model.provider '{provider_raw}' is unknown. "
                        f"Valid providers: {known_list}. "
                        f"Fix: run 'talaria config set model.provider <valid_provider>'"
                    )

            # Warn if model is set to a provider-prefixed name on a provider that doesn't use them
            provider_for_policy = runtime_provider or catalog_provider
            providers_accepting_vendor_slugs = {"custom", "auto", "lmstudio"}
            if (
                default_model
                and "/" in default_model
                and provider_for_policy
                and provider_for_policy not in providers_accepting_vendor_slugs
            ):
                check_warn(
                    f"model.default '{default_model}' uses a vendor/model slug but provider is '{provider_raw}'",
                    "(vendor-prefixed slugs belong to aggregators like openrouter)",
                )
                issues.append(
                    f"model.default '{default_model}' is vendor-prefixed but model.provider is '{provider_raw}'. "
                    "Either set model.provider to 'custom', or drop the vendor prefix."
                )

            # Check credentials for the configured provider.
            # Limit to API-key providers in PROVIDER_REGISTRY — other provider
            # types (OAuth, SDK, openrouter/anthropic/custom/auto) have their
            # own env-var checks elsewhere in doctor, and get_auth_status()
            # returns a bare {logged_in: False} for anything it doesn't
            # explicitly dispatch, which would produce false positives.
            if runtime_provider and runtime_provider not in ("auto", "custom"):
                try:
                    from talaria_cli.auth import PROVIDER_REGISTRY, get_auth_status
                    pconfig = PROVIDER_REGISTRY.get(runtime_provider)
                    if pconfig and getattr(pconfig, "auth_type", "") == "api_key":
                        status = get_auth_status(runtime_provider) or {}
                        configured = bool(
                            status.get("configured")
                            or status.get("logged_in")
                            or status.get("api_key")
                        )
                        if not configured:
                            check_fail(
                                f"model.provider '{runtime_provider}' is set but no API key is configured",
                                "(check ~/.talaria/.env or run 'talaria setup')",
                            )
                            issues.append(
                                f"No credentials found for provider '{runtime_provider}'. "
                                f"Run 'talaria setup' or set the provider's API key in {_DHH}/.env, "
                                f"or switch providers with 'talaria config set model.provider <name>'"
                            )
                except Exception:
                    pass

        except Exception as e:
            check_warn("Could not validate model/provider config", f"({e})")
    else:
        fallback_config = PROJECT_ROOT / 'cli-config.yaml'
        if fallback_config.exists():
            check_ok("cli-config.yaml exists (in project directory)")
        else:
            example_config = PROJECT_ROOT / 'cli-config.yaml.example'
            if should_fix and example_config.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(example_config), str(config_path))
                check_ok(f"Created {_DHH}/config.yaml from cli-config.yaml.example")
                fixed_count += 1
            elif should_fix:
                check_warn("config.yaml not found and no example to copy from")
                manual_issues.append(f"Create {_DHH}/config.yaml manually")
            else:
                check_warn("config.yaml not found", "(using defaults)")

    # Check config version and stale keys
    config_path = TALARIA_HOME / 'config.yaml'
    if config_path.exists():
        try:
            from talaria_cli.config import check_config_version, migrate_config
            current_ver, latest_ver = check_config_version()
            if current_ver < latest_ver:
                check_warn(
                    f"Config version outdated (v{current_ver} → v{latest_ver})",
                    "(new settings available)"
                )
                if should_fix:
                    try:
                        migrate_config(interactive=False, quiet=False)
                        check_ok("Config migrated to latest version")
                        fixed_count += 1
                    except Exception as mig_err:
                        check_warn(f"Auto-migration failed: {mig_err}")
                        issues.append("Run 'talaria setup' to migrate config")
                else:
                    issues.append("Run 'talaria doctor --fix' or 'talaria setup' to migrate config")
            else:
                check_ok(f"Config version up to date (v{current_ver})")
        except Exception:
            pass

        # Detect stale root-level model keys (known bug source — PR #4329)
        try:
            import yaml
            with open(config_path) as f:
                raw_config = yaml.safe_load(f) or {}
            stale_root_keys = [k for k in ("provider", "base_url") if k in raw_config and isinstance(raw_config[k], str)]
            if stale_root_keys:
                check_warn(
                    f"Stale root-level config keys: {', '.join(stale_root_keys)}",
                    "(should be under 'model:' section)"
                )
                if should_fix:
                    model_section = raw_config.setdefault("model", {})
                    for k in stale_root_keys:
                        if not model_section.get(k):
                            model_section[k] = raw_config.pop(k)
                        else:
                            raw_config.pop(k)
                    from utils import atomic_yaml_write
                    atomic_yaml_write(config_path, raw_config)
                    check_ok("Migrated stale root-level keys into model section")
                    fixed_count += 1
                else:
                    issues.append("Stale root-level provider/base_url in config.yaml — run 'talaria doctor --fix'")
        except Exception:
            pass

        # Validate config structure (catches malformed custom_providers, etc.)
        try:
            from talaria_cli.config import validate_config_structure
            config_issues = validate_config_structure()
            if config_issues:
                print()
                print(color("◆ Config Structure", Colors.CYAN, Colors.BOLD))
                for ci in config_issues:
                    if ci.severity == "error":
                        check_fail(ci.message)
                    else:
                        check_warn(ci.message)
                    # Show the hint indented
                    for hint_line in ci.hint.splitlines():
                        check_info(hint_line)
                    issues.append(ci.message)
        except Exception:
            pass

    # =========================================================================
    # Check: Auth providers
    # =========================================================================
    print()
    print(color("◆ Auth Providers", Colors.CYAN, Colors.BOLD))

    try:
        from talaria_cli.auth import (
            get_codex_auth_status,
            get_gemini_oauth_auth_status,
            get_minimax_oauth_auth_status,
        )

        codex_status = get_codex_auth_status()
        if codex_status.get("logged_in"):
            check_ok("OpenAI Codex auth", "(logged in)")
        else:
            check_warn("OpenAI Codex auth", "(not logged in)")
            if codex_status.get("error"):
                check_info(codex_status["error"])

        gemini_status = get_gemini_oauth_auth_status()
        if gemini_status.get("logged_in"):
            email = gemini_status.get("email") or ""
            project = gemini_status.get("project_id") or ""
            pieces = []
            if email:
                pieces.append(email)
            if project:
                pieces.append(f"project={project}")
            suffix = f" ({', '.join(pieces)})" if pieces else ""
            check_ok("Google Gemini OAuth", f"(logged in{suffix})")
        else:
            check_warn("Google Gemini OAuth", "(not logged in)")

        minimax_status = get_minimax_oauth_auth_status()
        if minimax_status.get("logged_in"):
            region = minimax_status.get("region", "global")
            check_ok("MiniMax OAuth", f"(logged in, region={region})")
        else:
            check_warn("MiniMax OAuth", "(not logged in)")
    except Exception as e:
        check_warn("Auth provider status", f"(could not check: {e})")

    if _safe_which("codex"):
        check_ok("codex CLI")
    else:
        # Native OAuth uses Talaria' own device-code flow — the Codex CLI is
        # only needed if you want to import existing tokens from
        # ~/.codex/auth.json.  Downgrade to info so users running
        # `talaria auth openai-codex` aren't told they're missing something.
        check_info(
            "codex CLI not installed "
            "(optional — only required to import tokens from an existing Codex CLI login)"
        )

    # =========================================================================
    # Check: Directory structure
    # =========================================================================
    print()
    print(color("◆ Directory Structure", Colors.CYAN, Colors.BOLD))
    
    talaria_home = TALARIA_HOME
    if talaria_home.exists():
        check_ok(f"{_DHH} directory exists")
    else:
        if should_fix:
            talaria_home.mkdir(parents=True, exist_ok=True)
            check_ok(f"Created {_DHH} directory")
            fixed_count += 1
        else:
            check_warn(f"{_DHH} not found", "(will be created on first use)")
    
    # Check expected subdirectories
    expected_subdirs = ["cron", "sessions", "logs", "skills", "memories"]
    for subdir_name in expected_subdirs:
        subdir_path = talaria_home / subdir_name
        if subdir_path.exists():
            check_ok(f"{_DHH}/{subdir_name}/ exists")
        else:
            if should_fix:
                subdir_path.mkdir(parents=True, exist_ok=True)
                check_ok(f"Created {_DHH}/{subdir_name}/")
                fixed_count += 1
            else:
                check_warn(f"{_DHH}/{subdir_name}/ not found", "(will be created on first use)")
    
    # Check for SOUL.md persona file
    soul_path = talaria_home / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        # Check if it's just the template comments (no real content)
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("<!--", "-->", "#"))]
        if lines:
            check_ok(f"{_DHH}/SOUL.md exists (persona configured)")
        else:
            check_info(f"{_DHH}/SOUL.md exists but is empty — edit it to customize personality")
    else:
        check_warn(f"{_DHH}/SOUL.md not found", "(create it to give Talaria a custom personality)")
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(
                "# Talaria Agent Persona\n\n"
                "<!-- Edit this file to customize how Talaria communicates. -->\n\n"
                "You are Talaria, a helpful AI assistant.\n",
                encoding="utf-8",
            )
            check_ok(f"Created {_DHH}/SOUL.md with basic template")
            fixed_count += 1
    
    # Check memory directory
    memories_dir = talaria_home / "memories"
    if memories_dir.exists():
        check_ok(f"{_DHH}/memories/ directory exists")
        memory_file = memories_dir / "MEMORY.md"
        user_file = memories_dir / "USER.md"
        if memory_file.exists():
            size = len(memory_file.read_text(encoding="utf-8").strip())
            check_ok(f"MEMORY.md exists ({size} chars)")
        else:
            check_info("MEMORY.md not created yet (will be created when the agent first writes a memory)")
        if user_file.exists():
            size = len(user_file.read_text(encoding="utf-8").strip())
            check_ok(f"USER.md exists ({size} chars)")
        else:
            check_info("USER.md not created yet (will be created when the agent first writes a memory)")
    else:
        check_warn(f"{_DHH}/memories/ not found", "(will be created on first use)")
        if should_fix:
            memories_dir.mkdir(parents=True, exist_ok=True)
            check_ok(f"Created {_DHH}/memories/")
            fixed_count += 1
    
    # Check SQLite session store
    state_db_path = talaria_home / "state.db"
    if state_db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(state_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            count = cursor.fetchone()[0]
            conn.close()
            check_ok(f"{_DHH}/state.db exists ({count} sessions)")
        except Exception as e:
            check_warn(f"{_DHH}/state.db exists but has issues: {e}")
    else:
        check_info(f"{_DHH}/state.db not created yet (will be created on first session)")

    # Check WAL file size (unbounded growth indicates missed checkpoints)
    wal_path = talaria_home / "state.db-wal"
    if wal_path.exists():
        try:
            wal_size = wal_path.stat().st_size
            if wal_size > 50 * 1024 * 1024:  # 50 MB
                check_warn(
                    f"WAL file is large ({wal_size // (1024*1024)} MB)",
                    "(may indicate missed checkpoints)"
                )
                if should_fix:
                    import sqlite3
                    conn = sqlite3.connect(str(state_db_path))
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.close()
                    new_size = wal_path.stat().st_size if wal_path.exists() else 0
                    check_ok(f"WAL checkpoint performed ({wal_size // 1024}K → {new_size // 1024}K)")
                    fixed_count += 1
                else:
                    issues.append("Large WAL file — run 'talaria doctor --fix' to checkpoint")
            elif wal_size > 10 * 1024 * 1024:  # 10 MB
                check_info(f"WAL file is {wal_size // (1024*1024)} MB (normal for active sessions)")
        except Exception:
            pass

    _check_gateway_service_linger(issues)

    # =========================================================================
    # Check: Command installation (talaria bin symlink)
    # =========================================================================
    if sys.platform != "win32":
        print()
        print(color("◆ Command Installation", Colors.CYAN, Colors.BOLD))

        # Determine the venv entry point location
        _venv_bin = None
        for _venv_name in ("venv", ".venv"):
            _candidate = PROJECT_ROOT / _venv_name / "bin" / "talaria"
            if _candidate.exists():
                _venv_bin = _candidate
                break

        # Determine the expected command link directory (mirrors install.sh logic)
        _prefix = os.environ.get("PREFIX", "")
        _is_termux_env = bool(os.environ.get("TERMUX_VERSION")) or "com.termux/files/usr" in _prefix
        if _is_termux_env and _prefix:
            _cmd_link_dir = Path(_prefix) / "bin"
            _cmd_link_display = "$PREFIX/bin"
        else:
            _cmd_link_dir = Path.home() / ".local" / "bin"
            _cmd_link_display = "~/.local/bin"
        _cmd_link = _cmd_link_dir / "talaria"

        if _venv_bin is None:
            check_warn(
                "Venv entry point not found",
                "(talaria not in venv/bin/ or .venv/bin/ — reinstall with pip install -e '.[all]')"
            )
            manual_issues.append(
                f"Reinstall entry point: cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e '.[all]'"
            )
        else:
            check_ok(f"Venv entry point exists ({_venv_bin.relative_to(PROJECT_ROOT)})")

            # Check the symlink at the command link location
            if _cmd_link.is_symlink():
                _target = _cmd_link.resolve()
                _expected = _venv_bin.resolve()
                if _target == _expected:
                    check_ok(f"{_cmd_link_display}/talaria → correct target")
                else:
                    check_warn(
                        f"{_cmd_link_display}/talaria points to wrong target",
                        f"(→ {_target}, expected → {_expected})"
                    )
                    if should_fix:
                        _cmd_link.unlink()
                        _cmd_link.symlink_to(_venv_bin)
                        check_ok(f"Fixed symlink: {_cmd_link_display}/talaria → {_venv_bin}")
                        fixed_count += 1
                    else:
                        issues.append(f"Broken symlink at {_cmd_link_display}/talaria — run 'talaria doctor --fix'")
            elif _cmd_link.exists():
                # It's a regular file, not a symlink — possibly a wrapper script
                check_ok(f"{_cmd_link_display}/talaria exists (non-symlink)")
            else:
                check_fail(
                    f"{_cmd_link_display}/talaria not found",
                    "(talaria command may not work outside the venv)"
                )
                if should_fix:
                    _cmd_link_dir.mkdir(parents=True, exist_ok=True)
                    _cmd_link.symlink_to(_venv_bin)
                    check_ok(f"Created symlink: {_cmd_link_display}/talaria → {_venv_bin}")
                    fixed_count += 1

                    # Check if the link dir is on PATH
                    _path_dirs = os.environ.get("PATH", "").split(os.pathsep)
                    if str(_cmd_link_dir) not in _path_dirs:
                        check_warn(
                            f"{_cmd_link_display} is not on your PATH",
                            "(add it to your shell config: export PATH=\"$HOME/.local/bin:$PATH\")"
                        )
                        manual_issues.append(f"Add {_cmd_link_display} to your PATH")
                else:
                    issues.append(f"Missing {_cmd_link_display}/talaria symlink — run 'talaria doctor --fix'")

    # =========================================================================
    # Check: External tools
    # =========================================================================
    print()
    print(color("◆ External Tools", Colors.CYAN, Colors.BOLD))
    
    # Git
    if _safe_which("git"):
        check_ok("git")
    else:
        check_warn("git not found", "(optional)")
    
    # ripgrep (optional, for faster file search)
    if _safe_which("rg"):
        check_ok("ripgrep (rg)", "(faster file search)")
    else:
        check_warn("ripgrep (rg) not found", "(file search uses grep fallback)")
        check_info(f"Install for faster search: {_system_package_install_cmd('ripgrep')}")
    
    # Docker (optional)
    terminal_env = os.getenv("TERMINAL_ENV", "local")
    if terminal_env == "docker":
        if _safe_which("docker"):
            # Check if docker daemon is running
            try:
                result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok("docker", "(daemon running)")
            else:
                check_fail("docker daemon not running")
                issues.append("Start Docker daemon")
        else:
            check_fail("docker not found", "(required for TERMINAL_ENV=docker)")
            issues.append("Install Docker or change TERMINAL_ENV")
    else:
        if _safe_which("docker"):
            check_ok("docker", "(optional)")
        else:
            if _is_termux():
                check_info("Docker backend is not available inside Termux (expected on Android)")
            else:
                check_warn("docker not found", "(optional)")
    
    # SSH (if using ssh backend)
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST")
        if ssh_host:
            # Try to connect
            try:
                result = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", ssh_host, "echo ok"],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok(f"SSH connection to {ssh_host}")
            else:
                check_fail(f"SSH connection to {ssh_host}")
                issues.append(f"Check SSH configuration for {ssh_host}")
        else:
            check_fail("TERMINAL_SSH_HOST not set", "(required for TERMINAL_ENV=ssh)")
            issues.append("Set TERMINAL_SSH_HOST in .env")
    
    # Node.js + agent-browser (for browser automation tools)
    if _safe_which("node"):
        check_ok("Node.js")
        # Check if agent-browser is installed
        agent_browser_path = PROJECT_ROOT / "node_modules" / "agent-browser"
        if agent_browser_path.exists():
            check_ok("agent-browser (Node.js)", "(browser automation)")
        else:
            if _is_termux():
                check_info("agent-browser is not installed (expected in the tested Termux path)")
                check_info("Install it manually later with: npm install -g agent-browser && agent-browser install")
                check_info("Termux browser setup:")
                for step in _termux_browser_setup_steps(node_installed=True):
                    check_info(step)
            else:
                check_warn("agent-browser not installed", "(run: npm install)")
    else:
        if _is_termux():
            check_info("Node.js not found (browser tools are optional in the tested Termux path)")
            check_info("Install Node.js on Termux with: pkg install nodejs")
            check_info("Termux browser setup:")
            for step in _termux_browser_setup_steps(node_installed=False):
                check_info(step)
        else:
            check_warn("Node.js not found", "(optional, needed for browser tools)")
    
    # npm audit for all Node.js packages
    if _safe_which("npm"):
        npm_dirs = [
            (PROJECT_ROOT, "Browser tools (agent-browser)"),
            (PROJECT_ROOT / "scripts" / "whatsapp-bridge", "WhatsApp bridge"),
        ]
        for npm_dir, label in npm_dirs:
            if not (npm_dir / "node_modules").exists():
                continue
            try:
                audit_result = subprocess.run(
                    ["npm", "audit", "--json"],
                    cwd=str(npm_dir),
                    capture_output=True, text=True, timeout=30,
                )
                import json as _json
                audit_data = _json.loads(audit_result.stdout) if audit_result.stdout.strip() else {}
                vuln_count = audit_data.get("metadata", {}).get("vulnerabilities", {})
                critical = vuln_count.get("critical", 0)
                high = vuln_count.get("high", 0)
                moderate = vuln_count.get("moderate", 0)
                total = critical + high + moderate
                if total == 0:
                    check_ok(f"{label} deps", "(no known vulnerabilities)")
                elif critical > 0 or high > 0:
                    check_warn(
                        f"{label} deps",
                        f"({critical} critical, {high} high, {moderate} moderate — run: cd {npm_dir} && npm audit fix)"
                    )
                    issues.append(f"{label} has {total} npm vulnerability(ies)")
                else:
                    check_ok(f"{label} deps", f"({moderate} moderate vulnerability(ies))")
            except Exception:
                pass

    # =========================================================================
    # Check: API connectivity
    # =========================================================================
    print()
    print(color("◆ API Connectivity", Colors.CYAN, Colors.BOLD))
    
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        print("  Checking OpenRouter API...", end="", flush=True)
        try:
            import httpx
            response = httpx.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {openrouter_key}"},
                timeout=10
            )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} OpenRouter API                          ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('(invalid API key)', Colors.DIM)}                ")
                issues.append("Check OPENROUTER_API_KEY in .env")
            elif response.status_code == 402:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('(out of credits — payment required)', Colors.DIM)}")
                issues.append(
                    "OpenRouter account has insufficient credits. "
                    "Fix: run 'talaria config set model.provider <provider>' to switch providers, "
                    "or fund your OpenRouter account at https://openrouter.ai/settings/credits"
                )
            elif response.status_code == 429:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('(rate limited)', Colors.DIM)}                ")
                issues.append("OpenRouter rate limit hit — consider switching to a different provider or waiting")
            else:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'(HTTP {response.status_code})', Colors.DIM)}                ")
        except Exception as e:
            print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'({e})', Colors.DIM)}                ")
            issues.append("Check network connectivity")
    else:
        check_warn("OpenRouter API", "(not configured)")
    
    from talaria_cli.auth import get_anthropic_key
    anthropic_key = get_anthropic_key()
    if anthropic_key:
        print("  Checking Anthropic API...", end="", flush=True)
        try:
            import httpx

            from agent.anthropic_adapter import (
                _COMMON_BETAS,
                _CONTEXT_1M_BETA,
                _OAUTH_ONLY_BETAS,
                _is_oauth_token,
            )

            headers = {"anthropic-version": "2023-06-01"}
            is_oauth = _is_oauth_token(anthropic_key)
            if is_oauth:
                headers["Authorization"] = f"Bearer {anthropic_key}"
                headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
            else:
                headers["x-api-key"] = anthropic_key
            response = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                timeout=10
            )
            # Reactive recovery: OAuth subscriptions that don't include 1M
            # context reject the request with 400 "long context beta is not
            # yet available for this subscription". Retry once with that
            # beta stripped so the doctor check doesn't falsely report the
            # Anthropic API as unreachable for those users.
            if (
                is_oauth
                and response.status_code == 400
                and "long context beta" in response.text.lower()
                and "not yet available" in response.text.lower()
            ):
                headers["anthropic-beta"] = ",".join(
                    [b for b in _COMMON_BETAS if b != _CONTEXT_1M_BETA] + list(_OAUTH_ONLY_BETAS)
                )
                response = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers=headers,
                    timeout=10,
                )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} Anthropic API                           ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} Anthropic API {color('(invalid API key)', Colors.DIM)}                 ")
            else:
                msg = "(couldn't verify)"
                print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(msg, Colors.DIM)}                 ")
        except Exception as e:
            print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(f'({e})', Colors.DIM)}                 ")

    # -- API-key providers --
    # Tuple: (name, env_vars, default_url, base_env, supports_models_endpoint)
    # If supports_models_endpoint is False, we skip the health check and just show "configured"
    _apikey_providers = [
        ("OpenAI", ("OPENAI_API_KEY",), "https://api.openai.com/v1/models", "OPENAI_BASE_URL", True),
        ("Xiaomi MiMo", ("XIAOMI_API_KEY",), "https://platform.xiaomimimo.com/openapi/v1/models", "XIAOMI_BASE_URL", True),
        ("LM Studio", ("LM_API_KEY",), None, "LM_BASE_URL", False),
    ]
    for _pname, _env_vars, _default_url, _base_env, _supports_health_check in _apikey_providers:
        _key = ""
        for _ev in _env_vars:
            _key = os.getenv(_ev, "")
            if _key:
                break
        if _key:
            _label = _pname.ljust(20)
            # Some providers (like MiniMax) don't support /models endpoint
            if not _supports_health_check:
                print(f"  {color('✓', Colors.GREEN)} {_label} {color('(key configured)', Colors.DIM)}")
                continue
            print(f"  Checking {_pname} API...", end="", flush=True)
            try:
                import httpx
                _base = os.getenv(_base_env, "") if _base_env else ""
                # Auto-detect Kimi Code keys (sk-kimi-) → api.kimi.com/coding/v1
                # (OpenAI-compat surface, which exposes /models for health check).
                if not _base and _key.startswith("sk-kimi-"):
                    _base = "https://api.kimi.com/coding/v1"
                # Anthropic-compat endpoints (/anthropic, api.kimi.com/coding
                # with no /v1) don't support /models.  Rewrite to the OpenAI-compat
                # /v1 surface for health checks.
                if _base and _base.rstrip("/").endswith("/anthropic"):
                    from agent.auxiliary_client import _to_openai_base_url
                    _base = _to_openai_base_url(_base)
                if base_url_host_matches(_base, "api.kimi.com") and _base.rstrip("/").endswith("/coding"):
                    _base = _base.rstrip("/") + "/v1"
                _url = (_base.rstrip("/") + "/models") if _base else _default_url
                _headers = {
                    "Authorization": f"Bearer {_key}",
                    "User-Agent": _TALARIA_USER_AGENT,
                }
                if base_url_host_matches(_base, "api.kimi.com"):
                    _headers["User-Agent"] = "claude-code/0.1.0"
                _resp = httpx.get(
                    _url,
                    headers=_headers,
                    timeout=10,
                )
                if _resp.status_code == 200:
                    print(f"\r  {color('✓', Colors.GREEN)} {_label}                          ")
                elif _resp.status_code == 401:
                    print(f"\r  {color('✗', Colors.RED)} {_label} {color('(invalid API key)', Colors.DIM)}           ")
                    issues.append(f"Check {_env_vars[0]} in .env")
                else:
                    print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'(HTTP {_resp.status_code})', Colors.DIM)}           ")
            except Exception as _e:
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'({_e})', Colors.DIM)}           ")

    # -- AWS Bedrock --
    # Bedrock uses the AWS SDK credential chain, not API keys.
    try:
        from agent.bedrock_adapter import has_aws_credentials, resolve_aws_auth_env_var, resolve_bedrock_region
        if has_aws_credentials():
            _auth_var = resolve_aws_auth_env_var()
            _region = resolve_bedrock_region()
            _label = "AWS Bedrock".ljust(20)
            print("  Checking AWS Bedrock...", end="", flush=True)
            try:
                import boto3
                _br_client = boto3.client("bedrock", region_name=_region)
                _br_resp = _br_client.list_foundation_models()
                _model_count = len(_br_resp.get("modelSummaries", []))
                print(f"\r  {color('✓', Colors.GREEN)} {_label} {color(f'({_auth_var}, {_region}, {_model_count} models)', Colors.DIM)}           ")
            except ImportError:
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'(boto3 not installed — {sys.executable} -m pip install boto3)', Colors.DIM)}           ")
                issues.append(f"Install boto3 for Bedrock: {sys.executable} -m pip install boto3")
            except Exception as _e:
                _err_name = type(_e).__name__
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'({_err_name}: {_e})', Colors.DIM)}           ")
                issues.append(f"AWS Bedrock: {_err_name} — check IAM permissions for bedrock:ListFoundationModels")
    except ImportError:
        pass  # bedrock_adapter not available — skip silently

    # =========================================================================
    # Check: Tool Availability
    # =========================================================================
    print()
    print(color("◆ Tool Availability", Colors.CYAN, Colors.BOLD))
    
    try:
        # Add project root to path for imports
        sys.path.insert(0, str(PROJECT_ROOT))
        from model_tools import TOOLSET_REQUIREMENTS, check_tool_availability
        
        available, unavailable = check_tool_availability()

        for tid in available:
            info = TOOLSET_REQUIREMENTS.get(tid, {})
            check_ok(info.get("name", tid))
        
        for item in unavailable:
            env_vars = item.get("missing_vars") or item.get("env_vars") or []
            if env_vars:
                vars_str = ", ".join(env_vars)
                check_warn(item["name"], f"(missing {vars_str})")
            else:
                check_warn(item["name"], "(system dependency not met)")

        # Count disabled tools with API key requirements
        api_disabled = [u for u in unavailable if (u.get("missing_vars") or u.get("env_vars"))]
        if api_disabled:
            issues.append("Run 'talaria setup' to configure missing API keys for full tool access")
    except Exception as e:
        check_warn("Could not check tool availability", f"({e})")
    
    # =========================================================================
    # Check: Skills Hub
    # =========================================================================
    print()
    print(color("◆ Skills Hub", Colors.CYAN, Colors.BOLD))

    hub_dir = TALARIA_HOME / "skills" / ".hub"
    if hub_dir.exists():
        check_ok("Skills Hub directory exists")
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            try:
                import json
                lock_data = json.loads(lock_file.read_text())
                count = len(lock_data.get("installed", {}))
                check_ok(f"Lock file OK ({count} hub-installed skill(s))")
            except Exception:
                check_warn("Lock file", "(corrupted or unreadable)")
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f"{q_count} skill(s) in quarantine", "(pending review)")
    else:
        check_warn("Skills Hub directory not initialized", "(run: talaria skills list)")

    from talaria_cli.config import get_env_value
    github_token = get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN")
    if github_token:
        check_ok("GitHub token configured (authenticated API access)")
    else:
        check_warn("No GITHUB_TOKEN", f"(60 req/hr rate limit — set in {_DHH}/.env for better rates)")

    # =========================================================================
    # Memory Provider (only check the active provider, if any)
    # =========================================================================
    print()
    print(color("◆ Memory Provider", Colors.CYAN, Colors.BOLD))

    _active_memory_provider = ""
    try:
        import yaml as _yaml
        _mem_cfg_path = TALARIA_HOME / "config.yaml"
        if _mem_cfg_path.exists():
            with open(_mem_cfg_path) as _f:
                _raw_cfg = _yaml.safe_load(_f) or {}
            _active_memory_provider = (_raw_cfg.get("memory") or {}).get("provider", "")
    except Exception:
        pass

    if not _active_memory_provider:
        check_ok("Built-in memory active", "(no external provider configured — this is fine)")
    else:
        # Generic check for other memory providers (openviking, hindsight, etc.)
        try:
            from plugins.memory import load_memory_provider
            _provider = load_memory_provider(_active_memory_provider)
            if _provider and _provider.is_available():
                check_ok(f"{_active_memory_provider} provider active")
            elif _provider:
                check_warn(f"{_active_memory_provider} configured but not available", "run: talaria memory status")
            else:
                check_warn(f"{_active_memory_provider} plugin not found", "run: talaria memory setup")
        except Exception as _e:
            check_warn(f"{_active_memory_provider} check failed", str(_e))

    # =========================================================================
    # Profiles
    # =========================================================================
    try:
        import re as _re

        from talaria_cli.profiles import _get_wrapper_dir, list_profiles, profile_exists

        named_profiles = [p for p in list_profiles() if not p.is_default]
        if named_profiles:
            print()
            print(color("◆ Profiles", Colors.CYAN, Colors.BOLD))
            check_ok(f"{len(named_profiles)} profile(s) found")
            wrapper_dir = _get_wrapper_dir()
            for p in named_profiles:
                parts = []
                if p.gateway_running:
                    parts.append("gateway running")
                if p.model:
                    parts.append(p.model[:30])
                if not (p.path / "config.yaml").exists():
                    parts.append("⚠ missing config")
                if not (p.path / ".env").exists():
                    parts.append("no .env")
                wrapper = wrapper_dir / p.name
                if not wrapper.exists():
                    parts.append("no alias")
                status = ", ".join(parts) if parts else "configured"
                check_ok(f"  {p.name}: {status}")

            # Check for orphan wrappers
            if wrapper_dir.is_dir():
                for wrapper in wrapper_dir.iterdir():
                    if not wrapper.is_file():
                        continue
                    try:
                        content = wrapper.read_text()
                        if "talaria -p" in content:
                            _m = _re.search(r"talaria -p (\S+)", content)
                            if _m and not profile_exists(_m.group(1)):
                                check_warn(f"Orphan alias: {wrapper.name} → profile '{_m.group(1)}' no longer exists")
                    except Exception:
                        pass
    except ImportError:
        pass
    except Exception:
        pass

    # =========================================================================
    # Summary
    # =========================================================================
    print()
    remaining_issues = issues + manual_issues
    if should_fix and fixed_count > 0:
        print(color("─" * 60, Colors.GREEN))
        print(color(f"  Fixed {fixed_count} issue(s).", Colors.GREEN, Colors.BOLD), end="")
        if remaining_issues:
            print(color(f" {len(remaining_issues)} issue(s) require manual intervention.", Colors.YELLOW, Colors.BOLD))
        else:
            print()
        print()
        if remaining_issues:
            for i, issue in enumerate(remaining_issues, 1):
                print(f"  {i}. {issue}")
            print()
    elif remaining_issues:
        print(color("─" * 60, Colors.YELLOW))
        print(color(f"  Found {len(remaining_issues)} issue(s) to address:", Colors.YELLOW, Colors.BOLD))
        print()
        for i, issue in enumerate(remaining_issues, 1):
            print(f"  {i}. {issue}")
        print()
        if not should_fix:
            print(color("  Tip: run 'talaria doctor --fix' to auto-fix what's possible.", Colors.DIM))
    else:
        print(color("─" * 60, Colors.GREEN))
        print(color("  All checks passed! 🎉", Colors.GREEN, Colors.BOLD))
    
    print()
