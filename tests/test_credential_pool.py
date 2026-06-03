"""Unit tests for the multi-credential failover pool.

Covers the pure helpers (timestamp/retry parsing, error normalization,
exhaustion cooldown math, dataclass round-trips) and the CredentialPool
selection strategies. Disk persistence is stubbed so no test touches
auth.json; strategy is set directly to avoid reading config.yaml.
"""

from __future__ import annotations

import time

import pytest

import agent.credential_pool as cp
from agent.credential_pool import (
    AUTH_TYPE_API_KEY,
    EXHAUSTED_TTL_429_SECONDS,
    EXHAUSTED_TTL_DEFAULT_SECONDS,
    STATUS_EXHAUSTED,
    STATUS_OK,
    STRATEGY_FILL_FIRST,
    STRATEGY_LEAST_USED,
    STRATEGY_RANDOM,
    CredentialPool,
    PooledCredential,
    _exhausted_ttl,
    _exhausted_until,
    _extract_retry_delay_seconds,
    _is_manual_source,
    _next_priority,
    _normalize_custom_pool_name,
    _normalize_error_context,
    _parse_absolute_timestamp,
)


def _cred(**overrides) -> PooledCredential:
    """Build a PooledCredential with sane defaults for tests."""
    base = dict(
        provider="anthropic",
        id=overrides.pop("id", "abc123"),
        label="test",
        auth_type=AUTH_TYPE_API_KEY,
        priority=0,
        source="manual",
        access_token="sk-test",
    )
    base.update(overrides)
    return PooledCredential(**base)


@pytest.fixture
def no_persist(monkeypatch):
    """Stop CredentialPool from writing to auth.json during tests."""
    monkeypatch.setattr(cp, "write_credential_pool", lambda *a, **k: None)


def _pool(entries, strategy=STRATEGY_FILL_FIRST, *, monkeypatch):
    # get_pool_strategy() reads config.yaml; pin it deterministically.
    monkeypatch.setattr(cp, "get_pool_strategy", lambda provider: strategy)
    return CredentialPool("anthropic", entries)


# --- _exhausted_ttl ---

def test_exhausted_ttl_429_and_default_both_one_hour():
    assert _exhausted_ttl(429) == EXHAUSTED_TTL_429_SECONDS == 3600
    assert _exhausted_ttl(402) == EXHAUSTED_TTL_DEFAULT_SECONDS == 3600
    assert _exhausted_ttl(None) == EXHAUSTED_TTL_DEFAULT_SECONDS


# --- _parse_absolute_timestamp ---

def test_parse_absolute_timestamp_epoch_seconds():
    assert _parse_absolute_timestamp(1_700_000_000) == 1_700_000_000.0


def test_parse_absolute_timestamp_epoch_millis_downscaled():
    # Values past the ms threshold are divided to seconds.
    assert _parse_absolute_timestamp(1_700_000_000_000) == 1_700_000_000.0


def test_parse_absolute_timestamp_iso8601_with_z():
    parsed = _parse_absolute_timestamp("2023-11-14T22:13:20Z")
    assert parsed is not None and parsed > 1_600_000_000


def test_parse_absolute_timestamp_numeric_string():
    assert _parse_absolute_timestamp("1700000000") == 1_700_000_000.0


@pytest.mark.parametrize("junk", [None, "", "   ", "not-a-date", 0, -5])
def test_parse_absolute_timestamp_rejects_junk(junk):
    assert _parse_absolute_timestamp(junk) is None


# --- _extract_retry_delay_seconds ---

def test_extract_retry_delay_quota_reset_ms():
    assert _extract_retry_delay_seconds('quotaResetDelay: 1500ms') == 1.5


def test_extract_retry_delay_quota_reset_s():
    assert _extract_retry_delay_seconds('quotaResetDelay: 30s') == 30.0


def test_extract_retry_delay_retry_after_phrase():
    assert _extract_retry_delay_seconds("please retry after 45 seconds") == 45.0


def test_extract_retry_delay_none_when_absent():
    assert _extract_retry_delay_seconds("plain rate limit message") is None
    assert _extract_retry_delay_seconds("") is None


# --- _normalize_error_context ---

def test_normalize_error_context_strips_reason_and_message():
    out = _normalize_error_context({"reason": "  quota  ", "message": "  boom  "})
    assert out["reason"] == "quota"
    assert out["message"] == "boom"


def test_normalize_error_context_reset_at_from_explicit_field():
    out = _normalize_error_context({"reset_at": 1_700_000_000})
    assert out["reset_at"] == 1_700_000_000.0


def test_normalize_error_context_reset_at_derived_from_message_delay():
    now = time.time()
    out = _normalize_error_context({"message": "retry after 10 seconds"})
    assert out["reset_at"] == pytest.approx(now + 10, abs=2)


def test_normalize_error_context_non_dict_returns_empty():
    assert _normalize_error_context(None) == {}
    assert _normalize_error_context("nope") == {}


# --- small helpers ---

def test_next_priority():
    assert _next_priority([]) == 0
    assert _next_priority([_cred(priority=0), _cred(priority=3)]) == 4


@pytest.mark.parametrize(
    "source,expected",
    [("manual", True), ("manual:cli", True), ("MANUAL", True), ("device_code", False), ("", False)],
)
def test_is_manual_source(source, expected):
    assert _is_manual_source(source) is expected


def test_normalize_custom_pool_name():
    assert _normalize_custom_pool_name("  Together AI ") == "together-ai"


# --- PooledCredential round-trip ---

def test_pooled_credential_round_trip_preserves_core_fields():
    cred = _cred(refresh_token="rt", base_url="https://x", request_count=7)
    restored = PooledCredential.from_dict("anthropic", cred.to_dict())
    assert restored.access_token == "sk-test"
    assert restored.refresh_token == "rt"
    assert restored.base_url == "https://x"
    assert restored.request_count == 7


def test_pooled_credential_extra_keys_round_trip():
    cred = _cred()
    cred.extra["client_id"] = "cid-123"  # client_id is an _EXTRA_KEYS member
    payload = cred.to_dict()
    assert payload["client_id"] == "cid-123"
    restored = PooledCredential.from_dict("anthropic", payload)
    assert restored.client_id == "cid-123"  # __getattr__ reads from extra


def test_pooled_credential_to_dict_always_emits_status_fields():
    payload = _cred().to_dict()
    # Status fields are emitted even when None so cooldown state persists.
    for key in ("last_status", "last_error_code", "last_error_reset_at"):
        assert key in payload


def test_pooled_credential_unknown_attr_raises():
    with pytest.raises(AttributeError):
        _ = _cred().definitely_not_a_field


# --- _exhausted_until ---

def test_exhausted_until_none_when_ok():
    assert _exhausted_until(_cred(last_status=STATUS_OK)) is None


def test_exhausted_until_uses_reset_at_when_present():
    cred = _cred(last_status=STATUS_EXHAUSTED, last_error_reset_at=1_700_000_000)
    assert _exhausted_until(cred) == 1_700_000_000.0


def test_exhausted_until_falls_back_to_status_at_plus_ttl():
    cred = _cred(last_status=STATUS_EXHAUSTED, last_status_at=1000.0, last_error_code=429)
    assert _exhausted_until(cred) == 1000.0 + EXHAUSTED_TTL_429_SECONDS


# --- CredentialPool selection ---

def test_pool_fill_first_picks_lowest_priority(monkeypatch, no_persist):
    pool = _pool([_cred(id="hi", priority=5), _cred(id="lo", priority=0)], monkeypatch=monkeypatch)
    selected = pool.select()
    assert selected is not None and selected.id == "lo"


def test_pool_skips_entry_in_cooldown(monkeypatch, no_persist):
    now = time.time()
    exhausted = _cred(
        id="dead",
        priority=0,
        last_status=STATUS_EXHAUSTED,
        last_status_at=now,
        last_error_code=429,
    )
    healthy = _cred(id="alive", priority=1)
    pool = _pool([exhausted, healthy], monkeypatch=monkeypatch)
    selected = pool.select()
    assert selected is not None and selected.id == "alive"
    assert pool.has_available() is True


def test_pool_no_available_when_all_in_cooldown(monkeypatch, no_persist):
    now = time.time()
    entries = [
        _cred(id="a", last_status=STATUS_EXHAUSTED, last_status_at=now, last_error_code=429),
        _cred(id="b", last_status=STATUS_EXHAUSTED, last_status_at=now, last_error_code=402),
    ]
    pool = _pool(entries, monkeypatch=monkeypatch)
    assert pool.select() is None
    assert pool.has_available() is False


def test_pool_clears_expired_cooldown_and_reselects(monkeypatch, no_persist):
    long_ago = time.time() - (EXHAUSTED_TTL_429_SECONDS + 60)
    entry = _cred(
        id="recovered",
        last_status=STATUS_EXHAUSTED,
        last_status_at=long_ago,
        last_error_code=429,
    )
    pool = _pool([entry], monkeypatch=monkeypatch)
    selected = pool.select()
    assert selected is not None and selected.id == "recovered"
    # Cooldown elapsed -> entry reset to OK in place.
    assert pool.entries()[0].last_status == STATUS_OK


def test_pool_least_used_picks_min_request_count(monkeypatch, no_persist):
    entries = [
        _cred(id="busy", priority=0, request_count=10),
        _cred(id="idle", priority=1, request_count=1),
    ]
    pool = _pool(entries, strategy=STRATEGY_LEAST_USED, monkeypatch=monkeypatch)
    selected = pool.select()
    assert selected is not None and selected.id == "idle"
    # Usage counter bumped so load redistributes on the next call.
    assert selected.request_count == 2


def test_pool_random_returns_a_member(monkeypatch, no_persist):
    entries = [_cred(id="a", priority=0), _cred(id="b", priority=1)]
    pool = _pool(entries, strategy=STRATEGY_RANDOM, monkeypatch=monkeypatch)
    selected = pool.select()
    assert selected is not None and selected.id in {"a", "b"}


def test_pool_empty_has_no_credentials(monkeypatch, no_persist):
    pool = _pool([], monkeypatch=monkeypatch)
    assert pool.has_credentials() is False
    assert pool.select() is None
