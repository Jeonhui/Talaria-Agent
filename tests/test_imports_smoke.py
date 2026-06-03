"""Import every first-party module.

Catches broken imports, circular imports, and syntax errors across the whole
tree — the cheapest possible guard against regressions like a deleted module
that's still referenced (e.g. the cli.py removal).

A missing *third-party* dependency (discord, anthropic, ...) is skipped, so the
test is meaningful locally without the full extras and exhaustive in CI where
``pip install -e .[dev]`` plus optional extras are present.
"""

import importlib
import pkgutil

import pytest

# Top-level names that belong to this repo. An ImportError whose missing module
# is rooted here is a REAL failure; anything else is an absent optional dep.
FIRST_PARTY_TOP = {
    "talaria_cli", "agent", "tools", "gateway", "cron", "acp_adapter", "plugins",
    "cli_config", "run_agent", "model_tools", "toolsets", "talaria_state",
    "talaria_constants", "talaria_time", "talaria_logging", "utils",
}

PACKAGES = ["talaria_cli", "agent", "tools", "gateway", "cron", "acp_adapter", "plugins"]
ROOT_MODULES = [
    "cli_config", "run_agent", "model_tools", "toolsets", "talaria_state",
    "talaria_constants", "talaria_time", "talaria_logging", "utils",
]

# Entry-point / side-effectful modules to skip (they run servers or __main__).
SKIP = {"acp_adapter.__main__", "acp_adapter.entry"}


def _all_module_names():
    names = list(ROOT_MODULES)
    for pkg_name in PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception:
            names.append(pkg_name)  # let the test report the failure
            continue
        for info in pkgutil.walk_packages(pkg.__path__, prefix=pkg_name + "."):
            names.append(info.name)
    return sorted(set(names))


def _is_missing_third_party(exc: BaseException) -> bool:
    name = getattr(exc, "name", None)
    if not name:
        return False
    return name.split(".")[0] not in FIRST_PARTY_TOP


@pytest.mark.parametrize("modname", _all_module_names())
def test_module_imports(modname):
    if modname in SKIP or modname.endswith(".__main__"):
        pytest.skip("entry-point module")
    try:
        importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        if _is_missing_third_party(exc):
            pytest.skip(f"optional dep missing: {exc.name}")
        raise
