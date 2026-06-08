"""Tests for the integration-module feature.

Covers: the loader, the example module (identity / tools / memory), the
runtime bridge (null-safety, identity caching, fail-closed semantics), and
the AIAgent MCP tool-allowlist filter.
"""

from __future__ import annotations

import os

import pytest

from agent.integration_module import IntegrationModule, UserInfo

# ---------------------------------------------------------------------------
# Loader + example module
# ---------------------------------------------------------------------------

def test_loader_discovers_example():
    from integrations import discover_integration_modules, load_integration_module

    names = [n for n, _desc, _ok in discover_integration_modules()]
    assert "example" in names

    mod = load_integration_module("example")
    assert mod is not None
    assert isinstance(mod, IntegrationModule)
    assert mod.name == "example"


def test_loader_unknown_module_returns_none():
    from integrations import load_integration_module

    assert load_integration_module("does-not-exist") is None


def test_example_is_available_requires_env(monkeypatch):
    from integrations import load_integration_module

    monkeypatch.delenv("EXAMPLE_MCP_URL", raising=False)
    monkeypatch.delenv("EXAMPLE_MCP_KEY", raising=False)
    mod = load_integration_module("example")
    assert mod.is_available() is False

    monkeypatch.setenv("EXAMPLE_MCP_URL", "https://mcp.example.com")
    monkeypatch.setenv("EXAMPLE_MCP_KEY", "sk-demo")
    assert mod.is_available() is True


def test_example_local_demo_authorization(monkeypatch):
    from integrations import load_integration_module

    monkeypatch.delenv("EXAMPLE_IDENTITY_URL", raising=False)
    monkeypatch.setenv("EXAMPLE_ALLOWED_USERS", "alice,bob")
    mod = load_integration_module("example")

    ok = mod.resolve_user(mcp_key="k", platform="telegram", user_id="alice")
    assert ok.authorized is True

    no = mod.resolve_user(mcp_key="k", platform="telegram", user_id="mallory")
    assert no.authorized is False


def test_example_http_failure_fails_closed(monkeypatch):
    from integrations import load_integration_module

    # Unreachable identity URL → must NOT authorize.
    monkeypatch.setenv("EXAMPLE_IDENTITY_URL", "http://127.0.0.1:1/none")
    monkeypatch.setenv("EXAMPLE_HTTP_TIMEOUT", "1")
    mod = load_integration_module("example")
    u = mod.resolve_user(mcp_key="k", platform="x", user_id="u")
    assert u.authorized is False


def test_example_available_tools_semantics():
    """None = no restriction, list = restrict, [] = zero."""
    from integrations import load_integration_module

    mod = load_integration_module("example")
    # demo grants "*" → None (no restriction)
    star = UserInfo(user_id="u", attributes={"_tools": "*"})
    assert mod.available_tools(star) is None
    # explicit list
    some = UserInfo(user_id="u", attributes={"_tools": ["search", "lookup"]})
    assert mod.available_tools(some) == ["search", "lookup"]


# ---------------------------------------------------------------------------
# Per-user memory (method B)
# ---------------------------------------------------------------------------

def test_example_memory_store_and_recall(tmp_path, monkeypatch):
    from integrations import load_integration_module

    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    mod = load_integration_module("example")

    alice = UserInfo(user_id="alice", name="Alice", authorized=True)
    bob = UserInfo(user_id="bob", name="Bob", authorized=True)

    mod.log_message(alice, "my favorite color is blue")
    mod.log_response(alice, "noted")
    mod.log_message(bob, "I live in Seoul")

    alice_files = mod.context_files(alice)
    recall = [f for f in alice_files if f.endswith(".md")]
    assert recall, "expected a recall file for alice"
    body = open(recall[0], encoding="utf-8").read()
    assert "blue" in body
    # isolation: alice's recall must not contain bob's content
    assert "Seoul" not in body


def test_example_memory_cap_trims(tmp_path, monkeypatch):
    from integrations import load_integration_module

    monkeypatch.setenv("TALARIA_HOME", str(tmp_path))
    monkeypatch.setenv("EXAMPLE_MEMORY_CAP", "10")
    mod = load_integration_module("example")
    u = UserInfo(user_id="heavy", authorized=True)

    for i in range(100):
        mod.log_message(u, f"message number {i} " + ("x" * 50))

    path = mod._mem_path("heavy")
    lines = path.read_text(encoding="utf-8").splitlines()
    # Trim is amortized (rewrites at >1.5×cap); never grows unbounded.
    assert len(lines) <= 15


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_module():
    """Install a fake active module into the bridge; restore after."""
    import gateway.integration_bridge as bridge

    saved = (bridge._module_loaded, bridge._module, dict(bridge._user_cache))
    bridge.reset_cache()
    yield bridge
    bridge._module_loaded, bridge._module, _ = saved
    bridge.reset_cache()


class _Src:
    def __init__(self, user_id, platform="telegram", user_name=""):
        self.platform = type("P", (), {"value": platform})()
        self.user_id = user_id
        self.user_name = user_name


def test_bridge_null_safe_without_module(fake_module):
    bridge = fake_module
    bridge._module_loaded = True
    bridge._module = None  # no active module

    src = _Src("alice")
    assert bridge.is_authorized(src) is None       # → caller falls back
    assert bridge.available_tools(src) is None     # → no restriction
    assert bridge.context_files(src) == []
    assert bridge.skills(src) == []
    assert bridge.mcp_server_config() == {}
    # logging is a no-op, must not raise
    bridge.log_message(src, "hi")
    bridge.log_response(src, "yo")


def _install(bridge, module):
    bridge._module_loaded = True
    bridge._module = module
    bridge.reset_cache()
    bridge._module_loaded = True
    bridge._module = module


def test_bridge_denied_not_cached_authorized_cached(fake_module):
    bridge = fake_module

    calls = {"n": 0}
    approved = {"v": False}

    class M(IntegrationModule):
        name = "t"
        def is_available(self): return True
        def mcp_url(self): return "u"
        def mcp_key(self): return "k"
        def resolve_user(self, *, mcp_key, platform, user_id, user_name="", **k):
            calls["n"] += 1
            return UserInfo(user_id=user_id, platform=platform,
                            authorized=approved["v"])
        def available_tools(self, user): return None

    _install(bridge, M())
    src = _Src("alice")

    # Denied → re-checked every call (not cached).
    assert bridge.is_authorized(src) is False
    assert bridge.is_authorized(src) is False
    assert calls["n"] == 2

    # Server approves → next call passes immediately, then cached.
    approved["v"] = True
    calls["n"] = 0
    assert bridge.is_authorized(src) is True
    assert bridge.is_authorized(src) is True
    assert calls["n"] == 1  # second hit served from cache


def test_bridge_available_tools_fail_closed_on_unresolved(fake_module):
    bridge = fake_module

    class M(IntegrationModule):
        name = "t"
        def is_available(self): return True
        def mcp_url(self): return "u"
        def mcp_key(self): return "k"
        def resolve_user(self, *, mcp_key, platform, user_id, user_name="", **k):
            raise RuntimeError("backend down")
        def available_tools(self, user): return None

    _install(bridge, M())
    # resolve fails → unresolved → fail-closed [] (zero MCP tools)
    assert bridge.available_tools(_Src("x")) == []


def test_bridge_mcp_server_config(fake_module):
    bridge = fake_module

    class M(IntegrationModule):
        name = "acme"
        def is_available(self): return True
        def mcp_url(self): return "https://mcp.acme.com"
        def mcp_key(self): return "sk-123"
        def resolve_user(self, *, mcp_key, platform, user_id, user_name="", **k):
            return UserInfo(user_id=user_id, authorized=True)
        def available_tools(self, user): return None

    _install(bridge, M())
    cfg = bridge.mcp_server_config()
    assert "acme" in cfg
    assert cfg["acme"]["url"] == "https://mcp.acme.com"
    assert cfg["acme"]["headers"]["Authorization"] == "Bearer sk-123"


# ---------------------------------------------------------------------------
# AIAgent MCP tool filter
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auth wiring (gateway/auth.is_user_authorized delegates to the module)
# ---------------------------------------------------------------------------

class _FakePairing:
    def is_approved(self, platform, user_id):
        return False

    def _is_rate_limited(self, platform, user_id):
        return False


def _src_real(user_id):
    from gateway.config import Platform
    from gateway.session import SessionSource

    return SessionSource(platform=Platform.TELEGRAM, chat_id=user_id, user_id=user_id)


def _module(authorized: bool):
    class M(IntegrationModule):
        name = "t"
        def is_available(self): return True
        def mcp_url(self): return "u"
        def mcp_key(self): return "k"
        def resolve_user(self, *, mcp_key, platform, user_id, user_name="", **k):
            return UserInfo(user_id=user_id, platform=platform, authorized=authorized)
        def available_tools(self, user): return None
    return M()


def test_auth_delegates_to_module_when_active(fake_module, monkeypatch):
    from gateway import auth

    _install(fake_module, _module(authorized=True))
    assert auth.is_user_authorized(_src_real("alice"), _FakePairing()) is True

    _install(fake_module, _module(authorized=False))
    # Module active + denied → False, regardless of env allowlist.
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "alice")
    assert auth.is_user_authorized(_src_real("alice"), _FakePairing()) is False


def test_auth_falls_back_to_env_without_module(fake_module, monkeypatch):
    from gateway import auth

    fake_module._module_loaded = True
    fake_module._module = None  # no module → env allowlist governs

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "alice")
    assert auth.is_user_authorized(_src_real("alice"), _FakePairing()) is True
    assert auth.is_user_authorized(_src_real("mallory"), _FakePairing()) is False


def test_filter_mcp_tools_intersection():
    from run_agent import _filter_mcp_tools_by_allowlist as f

    tools = [
        {"function": {"name": "read_file"}},        # built-in
        {"function": {"name": "mcp_x_search"}},
        {"function": {"name": "mcp_x_lookup"}},
        {"function": {"name": "mcp_x_delete"}},
    ]
    names = lambda ts: [t["function"]["name"] for t in ts]

    # built-ins always kept; only listed MCP tools survive
    assert names(f(tools, ["search", "lookup"])) == [
        "read_file", "mcp_x_search", "mcp_x_lookup",
    ]
    # intersection: a listed-but-unregistered tool simply isn't present
    assert names(f(tools, ["search", "refund"])) == ["read_file", "mcp_x_search"]
    # full prefixed name form
    assert names(f(tools, ["mcp_x_delete"])) == ["read_file", "mcp_x_delete"]
    # empty list → all MCP dropped, built-ins kept
    assert names(f(tools, [])) == ["read_file"]
