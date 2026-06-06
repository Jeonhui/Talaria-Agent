"""Tests for model-metadata pure helpers: name/URL parsing, error parsing,
context-probe tiers, and rough token estimation. All offline — no network."""

from __future__ import annotations

import pytest

from agent.model_metadata import (
    CONTEXT_PROBE_TIERS,
    _infer_provider_from_url,
    _is_custom_endpoint,
    _is_openrouter_base_url,
    _model_id_matches,
    _normalize_base_url,
    _normalize_model_version,
    _strip_provider_prefix,
    estimate_messages_tokens_rough,
    estimate_request_tokens_rough,
    estimate_tokens_rough,
    get_next_probe_tier,
    is_local_endpoint,
    parse_available_output_tokens_from_error,
    parse_context_limit_from_error,
)

# --- _strip_provider_prefix ---

def test_strip_known_provider_prefix():
    assert _strip_provider_prefix("local:my-model") == "my-model"


def test_strip_keeps_ollama_model_tag():
    # "qwen" is a provider prefix, but "0.5b" is an Ollama tag -> keep whole.
    assert _strip_provider_prefix("qwen:0.5b") == "qwen:0.5b"
    assert _strip_provider_prefix("deepseek:latest") == "deepseek:latest"


def test_strip_unknown_prefix_unchanged():
    assert _strip_provider_prefix("qwen3.5:27b") == "qwen3.5:27b"


def test_strip_no_colon_unchanged():
    assert _strip_provider_prefix("gpt-5") == "gpt-5"


def test_strip_http_unchanged():
    assert _strip_provider_prefix("http://x:8080") == "http://x:8080"


# --- base URL helpers ---

def test_normalize_base_url_strips_trailing_slash_and_space():
    assert _normalize_base_url("  https://x/api/  ") == "https://x/api"
    assert _normalize_base_url(None) == ""


def test_is_openrouter_base_url():
    assert _is_openrouter_base_url("https://openrouter.ai/api/v1") is True
    assert _is_openrouter_base_url("https://api.openai.com/v1") is False


def test_is_custom_endpoint():
    assert _is_custom_endpoint("https://api.together.ai") is True
    assert _is_custom_endpoint("https://openrouter.ai/api/v1") is False
    assert _is_custom_endpoint("") is False


@pytest.mark.parametrize(
    "url,provider",
    [
        ("https://api.openai.com/v1", "openai"),
        ("https://api.anthropic.com", "anthropic"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "alibaba"),
        ("https://openrouter.ai/api/v1", "openrouter"),
    ],
)
def test_infer_provider_from_url(url, provider):
    assert _infer_provider_from_url(url) == provider


def test_infer_provider_unknown_url():
    assert _infer_provider_from_url("https://example.com") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:8080",
        "http://192.168.1.50:1234",
        "http://10.0.0.5",
        "http://host.docker.internal:11434",
    ],
)
def test_is_local_endpoint_true(url):
    assert is_local_endpoint(url) is True


def test_is_local_endpoint_false_for_public():
    assert is_local_endpoint("https://api.openai.com") is False
    assert is_local_endpoint("") is False


# --- context probe tiers ---

def test_get_next_probe_tier_steps_down():
    assert get_next_probe_tier(200_000) == 128_000
    assert get_next_probe_tier(CONTEXT_PROBE_TIERS[0] + 1) == CONTEXT_PROBE_TIERS[0]


def test_get_next_probe_tier_none_at_minimum():
    assert get_next_probe_tier(CONTEXT_PROBE_TIERS[-1]) is None
    assert get_next_probe_tier(1) is None


# --- error parsing ---

@pytest.mark.parametrize(
    "msg,expected",
    [
        ("maximum context length is 32768 tokens", 32768),
        ("context_length_exceeded: 131072", 131072),
        ("model's max context length is 65536", 65536),
        # "X > Y maximum" — the real limit is Y, not the over-budget request X.
        ("250000 tokens > 200000 maximum", 200000),
    ],
)
def test_parse_context_limit(msg, expected):
    assert parse_context_limit_from_error(msg) == expected


def test_parse_context_limit_rejects_tiny_numbers():
    assert parse_context_limit_from_error("error code 42") is None


def test_parse_available_output_tokens():
    msg = ("max_tokens: 32768 > context_window: 200000 - "
           "input_tokens: 190000 = available_tokens: 10000")
    assert parse_available_output_tokens_from_error(msg) == 10000


def test_parse_available_output_tokens_ignores_prompt_length_error():
    assert parse_available_output_tokens_from_error("prompt is too long: 200000 tokens") is None


# --- model id / version matching ---

def test_model_id_exact_match():
    assert _model_id_matches("nemotron-49b", "nemotron-49b") is True


def test_model_id_slug_match():
    assert _model_id_matches("nvidia/nemotron-49b", "nemotron-49b") is True


def test_model_id_no_match():
    assert _model_id_matches("nvidia/other", "nemotron-49b") is False


def test_normalize_model_version_dots_to_dashes():
    assert _normalize_model_version("claude-opus-4.6") == "claude-opus-4-6"


# --- rough token estimation ---

def test_estimate_tokens_rough_ceiling_division():
    assert estimate_tokens_rough("") == 0
    assert estimate_tokens_rough("a") == 1       # ceil(1/4)
    assert estimate_tokens_rough("abcd") == 1
    assert estimate_tokens_rough("abcde") == 2   # ceil(5/4)


def test_estimate_messages_tokens_rough():
    msgs = [{"role": "user", "content": "hi"}]
    assert estimate_messages_tokens_rough(msgs) >= 1


def test_estimate_request_tokens_includes_tools_and_system():
    base = estimate_request_tokens_rough([{"role": "user", "content": "x"}])
    with_extras = estimate_request_tokens_rough(
        [{"role": "user", "content": "x"}],
        system_prompt="a long system prompt " * 10,
        tools=[{"name": "t", "schema": {"big": "payload" * 50}}],
    )
    assert with_extras > base
