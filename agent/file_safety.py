"""Shared file safety rules used by both tools and ACP shims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _talaria_home_path() -> Path:
    """Resolve the active TALARIA_HOME (profile-aware) without circular imports."""
    try:
        from talaria_constants import get_talaria_home  # local import to avoid cycles
        return get_talaria_home()
    except Exception:
        return Path(os.path.expanduser("~/.talaria"))


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written."""
    talaria_home = _talaria_home_path()
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "config"),
            str(talaria_home / ".env"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".zshrc"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".bash_profile"),
            os.path.join(home, ".zprofile"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    """Return sensitive directory prefixes that must never be written."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            "/etc/sudoers.d",
            "/etc/systemd",
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
        ]
    ]


def get_safe_write_root() -> Optional[str]:
    """Return the resolved TALARIA_WRITE_SAFE_ROOT path, or None if unset."""
    root = os.getenv("TALARIA_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def _is_remote_backend() -> bool:
    """Return True when the active terminal backend is not local (docker/ssh).

    On non-local backends the path argument to is_write_denied() refers to a
    location on the *remote* filesystem, so os.path.realpath() resolves
    against the host and produces a meaningless result. The deny-list patterns
    must still be applied — but only against the *unresolved* expanded path
    and against the path's basename so that writes to e.g. ~/.ssh/authorized_keys
    or /etc/passwd are caught even when the host cannot resolve the symlink chain.
    """
    backend = os.getenv("TERMINAL_ENV", "local").strip().lower()
    return backend not in ("local", "")


def is_write_denied(path: str) -> bool:
    """Return True if path is blocked by the write denylist or safe root.

    For local backends, the check is done against the realpath (symlink-resolved
    absolute path) on the host filesystem — the original behaviour.

    For non-local backends (docker, ssh) os.path.realpath() is meaningless
    because the path refers to a remote filesystem. In that case we check the
    *expanded but unresolved* path AND the basename against all deny-list
    entries so that canonical sensitive targets like ~/.ssh/authorized_keys or
    /etc/passwd are still blocked even though host-side symlink resolution
    cannot confirm them. This is the conservative/fail-safe choice.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    expanded = os.path.expanduser(str(path))

    if _is_remote_backend():
        # On remote backends realpath is unreliable — match against the
        # expanded path directly AND against each deny-list entry's expanded
        # (unresolved) form so the protection still fires.
        #
        # We also match against the basename so that a bare filename like
        # "authorized_keys" passed without a directory component is still
        # caught via prefix/exact checks built from the home dir.
        expanded_norm = os.path.normpath(expanded)
        basename = os.path.basename(expanded_norm)

        # Build deny sets from the unresolved expanded paths (no realpath)
        def _expand_only(p: str) -> str:
            return os.path.normpath(os.path.expanduser(str(p)))

        talaria_home = _talaria_home_path()
        raw_denied_paths = {
            _expand_only(p)
            for p in [
                os.path.join(home, ".ssh", "authorized_keys"),
                os.path.join(home, ".ssh", "id_rsa"),
                os.path.join(home, ".ssh", "id_ed25519"),
                os.path.join(home, ".ssh", "config"),
                str(talaria_home / ".env"),
                os.path.join(home, ".bashrc"),
                os.path.join(home, ".zshrc"),
                os.path.join(home, ".profile"),
                os.path.join(home, ".bash_profile"),
                os.path.join(home, ".zprofile"),
                os.path.join(home, ".netrc"),
                os.path.join(home, ".pgpass"),
                os.path.join(home, ".npmrc"),
                os.path.join(home, ".pypirc"),
                "/etc/sudoers",
                "/etc/passwd",
                "/etc/shadow",
            ]
        }
        raw_denied_prefixes = [
            _expand_only(p) + os.sep
            for p in [
                os.path.join(home, ".ssh"),
                os.path.join(home, ".aws"),
                os.path.join(home, ".gnupg"),
                os.path.join(home, ".kube"),
                "/etc/sudoers.d",
                "/etc/systemd",
                os.path.join(home, ".docker"),
                os.path.join(home, ".azure"),
                os.path.join(home, ".config", "gh"),
            ]
        ]

        if expanded_norm in raw_denied_paths:
            return True
        # Also match the basename against the denied path set so a bare
        # filename like "authorized_keys" is still caught.
        denied_basenames = {os.path.basename(p) for p in raw_denied_paths}
        if basename in denied_basenames and basename:
            return True
        for prefix in raw_denied_prefixes:
            if expanded_norm.startswith(prefix):
                return True

        # safe_root check using expanded path (realpath not usable remotely)
        safe_root_env = os.getenv("TALARIA_WRITE_SAFE_ROOT", "")
        if safe_root_env:
            safe_root_exp = os.path.normpath(os.path.expanduser(safe_root_env))
            if not (expanded_norm == safe_root_exp
                    or expanded_norm.startswith(safe_root_exp + os.sep)):
                return True

        return False

    # --- Local backend: original realpath-based logic ---
    resolved = os.path.realpath(expanded)

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    safe_root = get_safe_write_root()
    if safe_root and not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
        return True

    return False


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message when a read targets internal Talaria cache files."""
    resolved = Path(path).expanduser().resolve()
    talaria_home = _talaria_home_path().resolve()
    blocked_dirs = [
        talaria_home / "skills" / ".hub" / "index-cache",
        talaria_home / "skills" / ".hub",
    ]
    for blocked in blocked_dirs:
        try:
            resolved.relative_to(blocked)
        except ValueError:
            continue
        return (
            f"Access denied: {path} is an internal Talaria cache file "
            "and cannot be read directly to prevent prompt injection. "
            "Use the skills_list or skill_view tools instead."
        )
    return None
