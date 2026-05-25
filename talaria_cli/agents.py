"""`talaria agents` — multi-profile agent fleet control.

A thin convenience layer over ``--profile <name> gateway run``.  Each
profile is already a fully isolated TALARIA_HOME (own config.yaml,
.env, SOUL.md, skills/, state.db, checkpoints).  This module makes it
ergonomic to run several of them concurrently as independent agents.

Subcommands:

    talaria agents create A [--clone-from B] [--no-setup]
                                      # profile create + setup wizard in one go
    talaria agents list                # all profiles + running state
    talaria agents start A [B C ...]   # spawn detached gateway per profile
    talaria agents stop A              # stop one profile's gateway
    talaria agents stop --all          # stop every profile gateway
    talaria agents logs A [-f]         # tail one profile's agent.log
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _talaria_launcher_cmd() -> list[str]:
    """Return argv-prefix to invoke talaria, detached-safe.

    Prefers an installed ``talaria`` on PATH; falls back to running this
    repo's launcher script via the current Python interpreter so dev
    environments work without ``pip install -e``.
    """
    installed = shutil.which("talaria")
    if installed:
        return [installed]
    # Repo-local launcher: <repo>/talaria  (a thin Python wrapper around
    # talaria_cli.main.main()).  Found via the package's parent dir.
    pkg_dir = Path(__file__).resolve().parent  # talaria_cli/
    repo_root = pkg_dir.parent
    launcher = repo_root / "talaria"
    if launcher.is_file():
        return [sys.executable, str(launcher)]
    # Last resort: module invocation.
    return [sys.executable, "-m", "talaria_cli.main"]


def _profile_pid(profile_dir: Path) -> Optional[int]:
    try:
        from gateway.status import get_running_pid
    except Exception:
        return None
    try:
        return get_running_pid(profile_dir / "gateway.pid", cleanup_stale=False)
    except Exception:
        return None


def _resolve_profile(name: str):
    """Return (path, exists) for *name*. Imports lazily to avoid circular deps."""
    from talaria_cli.profiles import list_profiles
    for info in list_profiles():
        if info.name == name:
            return info, True
    return None, False


# ─── create ───────────────────────────────────────────────────────────────────


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new profile and (by default) run setup against it."""
    from talaria_cli.profiles import create_profile

    name: str = args.name
    clone_from: Optional[str] = args.clone_from
    skip_setup: bool = bool(args.no_setup)

    # Step 1: create the profile dir with config scaffolded from clone_from
    # (defaults to currently active profile when --clone-from omitted).
    try:
        profile_dir = create_profile(
            name,
            clone_from=clone_from,
            clone_config=True,
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"[{name}] created at {profile_dir}")

    if skip_setup:
        print(f"Next: talaria --profile {name} setup")
        return 0

    # Step 2: hand off to `talaria --profile <name> setup`. Inherits the
    # current TTY so the interactive wizard works.
    section: Optional[str] = args.section
    cmd = _talaria_launcher_cmd() + ["--profile", name, "setup"]
    if section:
        cmd.append(section)
    print(f"[{name}] launching setup wizard…")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        print()
        print(f"[{name}] setup interrupted — profile remains. Resume with:")
        print(f"    talaria --profile {name} setup")
        return 130


# ─── list ─────────────────────────────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> int:
    from talaria_cli.profiles import list_profiles

    profiles = list_profiles()
    if not profiles:
        print("No profiles found. Create one with: talaria profile create <name>")
        return 0

    name_w = max(8, max(len(p.name) for p in profiles))
    print(f"{'NAME':<{name_w}}  {'STATE':<8}  {'PID':>6}  {'SKILLS':>6}  MODEL")
    for p in profiles:
        pid = _profile_pid(p.path) if p.gateway_running else None
        state = "running" if pid else "stopped"
        pid_str = str(pid) if pid else "—"
        skills = f"{p.skill_count}"
        model = p.model or "—"
        if p.provider:
            model = f"{p.provider}/{model}"
        print(f"{p.name:<{name_w}}  {state:<8}  {pid_str:>6}  {skills:>6}  {model}")
    return 0


# ─── start ────────────────────────────────────────────────────────────────────


def _spawn_detached(profile_name: str, extra_args: list[str]) -> tuple[Optional[int], Optional[Path]]:
    """Spawn ``talaria --profile <name> gateway run`` detached.

    Returns ``(pid, log_path)`` — pid is the child's pid (parent of the
    new session) and log_path is where stdout/stderr were redirected.
    """
    info, _ = _resolve_profile(profile_name)
    if info is None:
        return None, None

    log_dir = info.path / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = info.path
    log_path = log_dir / "gateway.stdout.log"

    cmd = _talaria_launcher_cmd() + ["--profile", profile_name, "gateway", "run"] + list(extra_args)
    try:
        log_fp = open(log_path, "ab")
    except OSError:
        log_fp = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "TALARIA_HOME": str(info.path)},
        )
    finally:
        if log_fp is not subprocess.DEVNULL:
            try:
                log_fp.close()  # child inherits the open fd
            except Exception:
                pass

    return proc.pid, log_path


def cmd_start(args: argparse.Namespace) -> int:
    names: list[str] = list(args.profiles or [])
    if not names:
        print("error: at least one profile name required", file=sys.stderr)
        return 2

    rc = 0
    for name in names:
        info, ok = _resolve_profile(name)
        if not ok:
            print(f"[{name}] not found — skipping", file=sys.stderr)
            rc = 1
            continue

        existing = _profile_pid(info.path)
        if existing:
            print(f"[{name}] already running (PID {existing})")
            continue

        pid, log_path = _spawn_detached(name, [])
        if pid is None:
            print(f"[{name}] failed to spawn", file=sys.stderr)
            rc = 1
            continue

        # Brief settle so the child can write its PID file before we report
        time.sleep(0.5)
        actual = _profile_pid(info.path)
        if actual:
            print(f"[{name}] started (PID {actual}, logs → {log_path})")
        else:
            print(f"[{name}] spawned (PID {pid}, logs → {log_path}) — PID file not yet written")
    return rc


# ─── stop ─────────────────────────────────────────────────────────────────────


def _stop_one(name: str, info, *, force: bool, timeout: float) -> bool:
    pid = _profile_pid(info.path)
    if not pid:
        print(f"[{name}] not running")
        return True

    try:
        from gateway.status import terminate_pid
        terminate_pid(pid, force=force)
    except ProcessLookupError:
        print(f"[{name}] already gone")
        return True
    except Exception as exc:
        print(f"[{name}] terminate failed: {exc}", file=sys.stderr)
        return False

    # Wait for it to exit
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _profile_pid(info.path) is None:
            print(f"[{name}] stopped (PID {pid})")
            return True
        time.sleep(0.2)

    if force:
        print(f"[{name}] did not exit within {timeout:.0f}s (PID {pid})", file=sys.stderr)
        return False

    # Escalate to SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
        print(f"[{name}] force-killed (PID {pid})")
        return True
    except ProcessLookupError:
        return True
    except Exception as exc:
        print(f"[{name}] force-kill failed: {exc}", file=sys.stderr)
        return False


def cmd_stop(args: argparse.Namespace) -> int:
    from talaria_cli.profiles import list_profiles

    targets: list = []
    if args.all:
        targets = [p for p in list_profiles() if p.gateway_running]
        if not targets:
            print("No running agents.")
            return 0
    else:
        names = list(args.profiles or [])
        if not names:
            print("error: profile name(s) required, or pass --all", file=sys.stderr)
            return 2
        for name in names:
            info, ok = _resolve_profile(name)
            if not ok:
                print(f"[{name}] not found — skipping", file=sys.stderr)
                continue
            targets.append(info)

    rc = 0
    for info in targets:
        if not _stop_one(info.name, info, force=bool(args.force), timeout=float(args.timeout)):
            rc = 1
    return rc


# ─── logs ─────────────────────────────────────────────────────────────────────


def cmd_logs(args: argparse.Namespace) -> int:
    info, ok = _resolve_profile(args.profile)
    if not ok:
        print(f"profile not found: {args.profile}", file=sys.stderr)
        return 2

    cmd = _talaria_launcher_cmd() + ["--profile", info.name, "logs"]
    if args.follow:
        cmd.append("-f")
    if args.lines is not None:
        cmd += ["-n", str(args.lines)]
    if args.errors:
        cmd.append("errors")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130


# ─── argparse wiring ──────────────────────────────────────────────────────────


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Wire `talaria agents` subcommands."""
    parser.set_defaults(func=cmd_list)
    subs = parser.add_subparsers(dest="agents_command", metavar="COMMAND")

    p_create = subs.add_parser(
        "create",
        help="Create a new profile and run setup wizard against it",
        description=(
            "Two-step wrapper: `talaria profile create <name> --clone-config` "
            "followed by `talaria --profile <name> setup`.  The wizard "
            "inherits the current TTY so it's fully interactive."
        ),
    )
    p_create.add_argument("name", help="New profile name (lowercase / hyphens / underscores)")
    p_create.add_argument("--clone-from", default=None, metavar="PROFILE",
                          help="Source profile to clone config from (default: current active profile)")
    p_create.add_argument("--section", default=None,
                          choices=["model", "terminal", "gateway", "tools", "agent"],
                          help="Run only one setup section instead of the full wizard")
    p_create.add_argument("--no-setup", action="store_true",
                          help="Just create the profile; skip the setup wizard")
    p_create.set_defaults(func=cmd_create)

    p_list = subs.add_parser("list", help="Show all profiles + running state")
    p_list.set_defaults(func=cmd_list)

    p_start = subs.add_parser(
        "start",
        help="Spawn a detached gateway for one or more profiles",
        description=(
            "Spawns ``talaria --profile <name> gateway run`` for each "
            "named profile.  Each spawned process runs independently in "
            "its own session — closing the launching terminal does not "
            "affect them."
        ),
    )
    p_start.add_argument("profiles", nargs="+", metavar="PROFILE",
                         help="One or more profile names")
    p_start.set_defaults(func=cmd_start)

    p_stop = subs.add_parser("stop", help="Stop one or more profile gateways")
    p_stop.add_argument("profiles", nargs="*", metavar="PROFILE",
                        help="Profile names to stop (omit when using --all)")
    p_stop.add_argument("--all", action="store_true",
                        help="Stop every running profile gateway")
    p_stop.add_argument("-f", "--force", action="store_true",
                        help="Send SIGKILL immediately instead of SIGTERM+wait")
    p_stop.add_argument("--timeout", type=float, default=10.0,
                        help="Seconds to wait for SIGTERM before escalating (default 10)")
    p_stop.set_defaults(func=cmd_stop)

    p_logs = subs.add_parser("logs", help="Tail a profile's agent.log")
    p_logs.add_argument("profile", help="Profile name")
    p_logs.add_argument("-f", "--follow", action="store_true",
                        help="Follow the log in real time")
    p_logs.add_argument("-n", "--lines", type=int, default=None,
                        help="Number of lines to show (default 50)")
    p_logs.add_argument("--errors", action="store_true",
                        help="Show errors.log instead of agent.log")
    p_logs.set_defaults(func=cmd_logs)
