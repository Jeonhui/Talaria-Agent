"""Integration-module discovery and loading.

Integrations are a top-level core feature (not a plugin). Modules are
discovered from two directories:

1. Bundled:        ``integrations/<name>/`` (shipped with talaria)
2. User-installed:  ``$TALARIA_HOME/integrations/<name>/``

Each subdirectory must contain ``__init__.py`` exposing a class that
implements the :class:`~agent.integration_module.IntegrationModule` ABC,
either via a top-level subclass or a ``register(ctx)`` function that calls
``ctx.register_integration_module(instance)``.

Only ONE module is active at a time, selected via ``integration.module``
in config.yaml.

Usage:
    from integrations import (
        discover_integration_modules,
        load_integration_module,
    )

    available = discover_integration_modules()   # [(name, desc, ok), ...]
    module = load_integration_module("example")   # IntegrationModule | None
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from agent.integration_module import IntegrationModule

logger = logging.getLogger(__name__)

_INTEGRATION_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def _get_user_integrations_dir() -> Optional[Path]:
    """Return ``$TALARIA_HOME/integrations/`` or None if unavailable."""
    try:
        from talaria_constants import get_talaria_home
        d = get_talaria_home() / "integrations"
        return d if d.is_dir() else None
    except Exception:
        return None


def _is_integration_module_dir(path: Path) -> bool:
    """Cheap text scan: does *path* look like an integration module?"""
    init_file = path / "__init__.py"
    if not init_file.exists():
        return False
    try:
        source = init_file.read_text(errors="replace")[:8192]
        return "register_integration_module" in source or "IntegrationModule" in source
    except Exception:
        return False


def _iter_module_dirs() -> List[Tuple[str, Path]]:
    """Yield ``(name, path)`` for all discovered module directories.

    Bundled first, then user-installed. Bundled wins on name collision.
    """
    seen: set = set()
    dirs: List[Tuple[str, Path]] = []

    if _INTEGRATION_DIR.is_dir():
        for child in sorted(_INTEGRATION_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if not (child / "__init__.py").exists():
                continue
            seen.add(child.name)
            dirs.append((child.name, child))

    user_dir = _get_user_integrations_dir()
    if user_dir:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if child.name in seen:
                continue
            if not _is_integration_module_dir(child):
                continue
            dirs.append((child.name, child))

    return dirs


def find_module_dir(name: str) -> Optional[Path]:
    """Resolve a module name to its directory (bundled first)."""
    bundled = _INTEGRATION_DIR / name
    if bundled.is_dir() and (bundled / "__init__.py").exists():
        return bundled
    user_dir = _get_user_integrations_dir()
    if user_dir:
        user = user_dir / name
        if user.is_dir() and _is_integration_module_dir(user):
            return user
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_integration_modules() -> List[Tuple[str, str, bool]]:
    """Return ``(name, description, is_available)`` for each module found."""
    results = []
    for name, child in _iter_module_dirs():
        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass

        available = True
        try:
            module = _load_module_from_dir(child)
            available = module.is_available() if module else False
        except Exception:
            available = False

        results.append((name, desc, available))
    return results


def load_integration_module(name: str) -> Optional["IntegrationModule"]:
    """Load and return an IntegrationModule instance by name, or None."""
    module_dir = find_module_dir(name)
    if not module_dir:
        logger.debug("Integration module '%s' not found", name)
        return None
    try:
        module = _load_module_from_dir(module_dir)
        if module:
            return module
        logger.warning("Integration module '%s' loaded but no instance found", name)
        return None
    except Exception as e:
        logger.warning("Failed to load integration module '%s': %s", name, e)
        return None


def get_active_integration_module() -> Optional["IntegrationModule"]:
    """Load the module named by ``integration.module`` in config, or None."""
    name = _get_active_module_name()
    if not name:
        return None
    return load_integration_module(name)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_module_from_dir(module_dir: Path) -> Optional["IntegrationModule"]:
    """Import a module directory and extract the IntegrationModule instance."""
    name = module_dir.name
    _is_bundled = (
        _INTEGRATION_DIR in module_dir.parents
        or module_dir.parent == _INTEGRATION_DIR
    )
    module_name = (
        f"integrations.{name}" if _is_bundled
        else f"_talaria_user_integration.{name}"
    )
    init_file = module_dir / "__init__.py"
    if not init_file.exists():
        return None

    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(
            module_name, str(init_file),
            submodule_search_locations=[str(module_dir)],
        )
        if not spec:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # Register sibling .py files so relative imports resolve.
        for sub_file in module_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            full_sub_name = f"{module_name}.{sub_file.stem}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug("Failed to load submodule %s: %s", full_sub_name, e)

        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug("Failed to exec_module %s: %s", module_name, e)
            sys.modules.pop(module_name, None)
            return None

    # register(ctx) pattern first.
    if hasattr(mod, "register"):
        collector = _ModuleCollector()
        try:
            mod.register(collector)
            if collector.module:
                return collector.module
        except Exception as e:
            logger.debug("register() failed for %s: %s", name, e)

    # Fallback: find an IntegrationModule subclass and instantiate it.
    from agent.integration_module import IntegrationModule
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (isinstance(attr, type) and issubclass(attr, IntegrationModule)
                and attr is not IntegrationModule):
            try:
                return attr()
            except Exception:
                pass
    return None


class _ModuleCollector:
    """Fake plugin context that captures register_integration_module()."""

    def __init__(self):
        self.module = None

    def register_integration_module(self, module):
        self.module = module

    # No-ops for other registration methods.
    def register_tool(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass


def _get_active_module_name() -> Optional[str]:
    """Read ``integration.module`` from config.yaml, or None."""
    try:
        from talaria_cli.config import cfg_get, load_config
        return cfg_get(load_config(), "integration", "module") or None
    except Exception:
        return None
