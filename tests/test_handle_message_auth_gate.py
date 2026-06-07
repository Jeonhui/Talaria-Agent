"""Gateway message-pipeline auth gate (gateway/run.py::GatewayRunner._handle_message).

End-to-end coverage of the early-exit branches inside ``_handle_message``:

- Internal events (background-process notifications) bypass auth.
- Messages with no ``user_id`` are silently dropped.
- Unauthorized senders in groups are silently dropped (no pairing code).
- Unauthorized DMs honor the configured ``unauthorized_dm_behavior``
  (``ignore`` skips the pairing flow, ``pair`` triggers a code).
- The pairing flow is rate-limited by ``pairing_store``.

These are the hardening guarantees the gateway makes against unsolicited
senders — locking them down covers the test-gap that
``docs/REFACTOR-ROADMAP.md`` flags as the prerequisite for phases A2 and
A3.

``GatewayRunner.__init__`` is intentionally bypassed (it spins up an
async event loop, loads SQLite, etc.); the runner exposes class-level
defaults specifically so tests can build a partial instance — see the
comment on ``class GatewayRunner`` in ``gateway/run.py``.
"""

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.config import Platform  # noqa: E402
from gateway.platforms.base import MessageEvent  # noqa: E402
from gateway.run import GatewayRunner  # noqa: E402
from gateway.session import SessionSource  # noqa: E402

# ── Fakes ──────────────────────────────────────────────────────────────────


@dataclass
class _SentMessage:
    chat_id: str
    text: str


class FakeAdapter:
    """Records every adapter.send() so tests can assert what the pairing
    flow sent (or didn't send)."""

    def __init__(self):
        self.sent: list[_SentMessage] = []

    async def send(self, chat_id: str, text: str):
        self.sent.append(_SentMessage(chat_id, text))


class FakePairingStore:
    def __init__(self, approved=None, *, rate_limited=False, code="ABC123"):
        self._approved = set(approved or [])
        self._rate_limited = rate_limited
        self._code = code
        self.recorded_rate_limit = False

    def is_approved(self, platform: str, user_id: str) -> bool:
        return (platform, user_id) in self._approved

    def _is_rate_limited(self, platform: str, user_id: str) -> bool:
        return self._rate_limited

    def generate_code(self, platform: str, user_id: str, user_name: str) -> str:
        return self._code

    def _record_rate_limit(self, platform: str, user_id: str) -> None:
        self.recorded_rate_limit = True


# ── Fixtures ───────────────────────────────────────────────────────────────


def _make_runner(*, pairing_store=None, adapter=None, platform=Platform.TELEGRAM):
    """Build a partially-initialized GatewayRunner for unit testing.

    Skips ``__init__`` (which would spin up SQLite + an asyncio event loop)
    and wires only the attributes that ``_handle_message`` actually reads
    along the early-exit paths under test.
    """
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = None
    runner.adapters = {platform: adapter} if adapter else {}
    runner.pairing_store = pairing_store or FakePairingStore()
    runner.session_store = SimpleNamespace()
    runner._update_prompt_pending = {}

    # _session_key_for_source is called early in _handle_message; the
    # exact value doesn't matter to the auth gate, only that it's hashable.
    runner._session_key_for_source = lambda source: f"key:{source.chat_id}"
    return runner


def _event(*, internal=False, user_id="100", chat_type="dm", chat_id="100",
           platform=Platform.TELEGRAM, text="hi") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=platform,
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
        ),
        internal=internal,
    )


def _drop_plugin_hooks(monkeypatch):
    """Avoid pulling the real plugin host into unit tests — return [] from
    ``invoke_hook`` so the pre_gateway_dispatch flow is a no-op."""
    import talaria_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "invoke_hook", lambda *a, **kw: [])


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_without_user_id_is_dropped(monkeypatch):
    """Telegram service messages and anonymous-admin actions have no user_id;
    the pipeline must drop them silently rather than triggering pairing."""
    _drop_plugin_hooks(monkeypatch)
    runner = _make_runner()
    evt = _event(user_id=None)

    result = await runner._handle_message(evt)

    assert result is None


@pytest.mark.asyncio
async def test_unauthorized_group_message_is_silently_dropped(monkeypatch):
    """Groups never receive pairing codes — only DMs do."""
    _drop_plugin_hooks(monkeypatch)
    adapter = FakeAdapter()
    runner = _make_runner(adapter=adapter)
    evt = _event(chat_type="group", chat_id="-100", user_id="999")

    result = await runner._handle_message(evt)

    assert result is None
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_unauthorized_dm_under_ignore_policy_drops_silently(monkeypatch):
    """When ``unauthorized_dm_behavior`` resolves to ``ignore``
    (e.g. because an allowlist is configured), no pairing code goes out."""
    _drop_plugin_hooks(monkeypatch)
    # An allowlist exists → auth fails → unauthorized_dm_behavior falls to "ignore" per #9337
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    adapter = FakeAdapter()
    runner = _make_runner(adapter=adapter)
    evt = _event(user_id="999")

    result = await runner._handle_message(evt)

    assert result is None
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_unauthorized_dm_under_pair_policy_sends_pairing_code(monkeypatch):
    """No allowlist configured → behavior is the open-gateway default
    ``pair`` → the adapter receives a pairing code."""
    _drop_plugin_hooks(monkeypatch)
    adapter = FakeAdapter()
    pairing = FakePairingStore(code="WELCOME")
    runner = _make_runner(adapter=adapter, pairing_store=pairing)
    evt = _event(user_id="999")

    result = await runner._handle_message(evt)

    assert result is None
    assert len(adapter.sent) == 1
    sent = adapter.sent[0]
    assert sent.chat_id == "100"
    assert "WELCOME" in sent.text
    assert "talaria pairing approve telegram WELCOME" in sent.text


@pytest.mark.asyncio
async def test_unauthorized_dm_rate_limited_skips_pairing_send(monkeypatch):
    """When the pairing store reports the sender as rate-limited, the
    adapter MUST NOT receive another code — protects unauthorised senders
    from being spammed during DM bursts."""
    _drop_plugin_hooks(monkeypatch)
    adapter = FakeAdapter()
    pairing = FakePairingStore(rate_limited=True)
    runner = _make_runner(adapter=adapter, pairing_store=pairing)
    evt = _event(user_id="999")

    result = await runner._handle_message(evt)

    assert result is None
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_unauthorized_dm_pair_records_rate_limit_when_codes_exhausted(monkeypatch):
    """If the pairing store returns an empty code (the per-user code pool is
    exhausted), the adapter sends the friendly fallback message and a rate-
    limit is recorded so further attempts are silent."""
    _drop_plugin_hooks(monkeypatch)
    adapter = FakeAdapter()
    pairing = FakePairingStore(code="")  # exhausted
    runner = _make_runner(adapter=adapter, pairing_store=pairing)
    evt = _event(user_id="999")

    result = await runner._handle_message(evt)

    assert result is None
    assert pairing.recorded_rate_limit is True
    assert len(adapter.sent) == 1
    assert "later" in adapter.sent[0].text.lower()


# ── pytest-asyncio configuration ───────────────────────────────────────────
# These tests use ``@pytest.mark.asyncio`` directly; no module-level
# ``asyncio_mode`` is required when the marker is explicit per-test.


@pytest.fixture(scope="module")
def event_loop():
    """A per-module loop keeps tests deterministic when xdist runs them
    in worker processes."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
