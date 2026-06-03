"""Tests for usage normalization and cost estimation (money math)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import agent.usage_pricing as up
from agent.usage_pricing import (
    CanonicalUsage,
    PricingEntry,
    _to_decimal,
    _to_int,
    estimate_usage_cost,
    normalize_usage,
    resolve_billing_route,
)

# --- _to_decimal / _to_int ---

def test_to_decimal_parses_numbers_and_strings():
    assert _to_decimal(3) == Decimal("3")
    assert _to_decimal("1.5") == Decimal("1.5")


def test_to_decimal_none_and_junk():
    assert _to_decimal(None) is None
    assert _to_decimal("not-a-number") is None


def test_to_int_coerces_and_defaults_zero():
    assert _to_int("5") == 5
    assert _to_int(None) == 0
    assert _to_int("junk") == 0


# --- resolve_billing_route ---

def test_route_codex_is_subscription_included():
    route = resolve_billing_route("gpt-5", provider="openai-codex")
    assert route.billing_mode == "subscription_included"


def test_route_openrouter_by_provider():
    route = resolve_billing_route("anthropic/claude", provider="openrouter")
    assert route.provider == "openrouter"
    assert route.billing_mode == "official_models_api"


def test_route_openrouter_by_base_url():
    route = resolve_billing_route("x", base_url="https://openrouter.ai/api/v1")
    assert route.provider == "openrouter"


def test_route_anthropic_strips_slug_prefix():
    route = resolve_billing_route("anthropic/claude-opus-4", provider="anthropic")
    assert route.model == "claude-opus-4"
    assert route.billing_mode == "official_docs_snapshot"


def test_route_infers_provider_from_model_slug():
    route = resolve_billing_route("openai/gpt-5")
    assert route.provider == "openai"
    assert route.model == "gpt-5"


def test_route_custom_local_is_unknown():
    assert resolve_billing_route("m", provider="local").billing_mode == "unknown"
    assert resolve_billing_route("m", base_url="http://localhost:1234").billing_mode == "unknown"


# --- normalize_usage ---

def test_normalize_anthropic_shape():
    raw = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=50,
        cache_creation_input_tokens=10,
    )
    out = normalize_usage(raw, provider="anthropic")
    assert out.input_tokens == 100
    assert out.output_tokens == 20
    assert out.cache_read_tokens == 50
    assert out.cache_write_tokens == 10


def test_normalize_codex_subtracts_cache_from_input_total():
    raw = SimpleNamespace(
        input_tokens=1000,  # total, includes cache
        output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=300, cache_creation_tokens=100),
    )
    out = normalize_usage(raw, api_mode="codex_responses")
    assert out.input_tokens == 600  # 1000 - 300 - 100
    assert out.cache_read_tokens == 300
    assert out.cache_write_tokens == 100


def test_normalize_openai_with_details():
    raw = SimpleNamespace(
        prompt_tokens=500,
        completion_tokens=80,
        prompt_tokens_details=SimpleNamespace(cached_tokens=200),
    )
    out = normalize_usage(raw, provider="openai")
    assert out.input_tokens == 300  # 500 - 200
    assert out.cache_read_tokens == 200


def test_normalize_openai_falls_back_to_top_level_cache_fields():
    # Proxy exposes Anthropic-style top-level cache fields, no details object.
    raw = SimpleNamespace(
        prompt_tokens=500,
        completion_tokens=10,
        prompt_tokens_details=None,
        cache_read_input_tokens=150,
        cache_creation_input_tokens=50,
    )
    out = normalize_usage(raw)
    assert out.cache_read_tokens == 150
    assert out.cache_write_tokens == 50
    assert out.input_tokens == 300  # 500 - 150 - 50


def test_normalize_empty_usage_returns_zeros():
    out = normalize_usage(None)
    assert out.input_tokens == 0 and out.output_tokens == 0


def test_canonical_usage_token_properties():
    u = CanonicalUsage(input_tokens=100, cache_read_tokens=50, cache_write_tokens=10, output_tokens=20)
    assert u.prompt_tokens == 160
    assert u.total_tokens == 180


# --- estimate_usage_cost ---

def test_estimate_cost_subscription_included_is_zero(monkeypatch):
    result = estimate_usage_cost("gpt-5", CanonicalUsage(input_tokens=1000), provider="openai-codex")
    assert result.status == "included"
    assert result.amount_usd == Decimal("0")


def test_estimate_cost_unknown_when_no_pricing(monkeypatch):
    monkeypatch.setattr(up, "get_pricing_entry", lambda *a, **k: None)
    result = estimate_usage_cost("mystery", CanonicalUsage(input_tokens=1000), provider="anthropic")
    assert result.status == "unknown"
    assert result.amount_usd is None


def test_estimate_cost_arithmetic(monkeypatch):
    entry = PricingEntry(
        input_cost_per_million=Decimal("3.00"),
        output_cost_per_million=Decimal("15.00"),
        cache_read_cost_per_million=Decimal("0.30"),
        cache_write_cost_per_million=Decimal("3.75"),
        source="official_docs",
    )
    monkeypatch.setattr(up, "get_pricing_entry", lambda *a, **k: entry)
    usage = CanonicalUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    result = estimate_usage_cost("claude", usage, provider="anthropic")
    # 3.00 + 15.00 + 0.30 + 3.75 per million each (1M tokens).
    assert result.amount_usd == Decimal("22.05")
    assert result.status == "estimated"


def test_estimate_cost_unknown_when_input_priced_but_rate_missing(monkeypatch):
    entry = PricingEntry(input_cost_per_million=None, output_cost_per_million=Decimal("1"), source="official_docs")
    monkeypatch.setattr(up, "get_pricing_entry", lambda *a, **k: entry)
    result = estimate_usage_cost("x", CanonicalUsage(input_tokens=100), provider="anthropic")
    assert result.status == "unknown"
