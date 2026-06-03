"""Payload sanitizers (agent/message_sanitizer) — run before hitting a provider."""

import agent.message_sanitizer as s


def test_lone_surrogate_replaced():
    out = s._sanitize_surrogates("hi\ud800x")
    assert "\ud800" not in out
    assert out.startswith("hi") and out.endswith("x")


def test_clean_text_unchanged():
    assert s._sanitize_surrogates("normal text 한국어 😀") == "normal text 한국어 😀"


def test_repair_empty_args_to_empty_object():
    assert s._repair_tool_call_arguments("", "tool") == "{}"


def test_repair_python_none_to_empty_object():
    assert s._repair_tool_call_arguments("None", "tool") == "{}"


def test_repair_preserves_valid_json_semantics():
    import json
    # Valid JSON is re-serialized (whitespace normalized) but stays equivalent.
    assert json.loads(s._repair_tool_call_arguments('{"a": 1}', "tool")) == {"a": 1}


def test_strip_non_ascii():
    out = s._strip_non_ascii("café​")  # accented + zero-width space
    assert all(ord(ch) < 128 for ch in out)


def test_sanitize_messages_surrogates_mutates_in_place():
    msgs = [{"role": "user", "content": "bad\ud834text"}]
    changed = s._sanitize_messages_surrogates(msgs)
    assert changed is True
    assert "\ud834" not in msgs[0]["content"]
