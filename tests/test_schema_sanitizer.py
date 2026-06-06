"""Tests for tool-schema sanitization (llama.cpp / Anthropic compatibility).

These guard the known-hostile JSON-Schema shapes documented in
tools/schema_sanitizer.py — bare-string schemas, array types, nullable
unions, propertyless objects, and dangling ``required`` entries — that
otherwise make strict backends reject the whole request with HTTP 400.
"""

from __future__ import annotations

from tools.schema_sanitizer import (
    _sanitize_node,
    sanitize_tool_schemas,
    strip_nullable_unions,
)


def _params(tool):
    return tool["function"]["parameters"]


# --- sanitize_tool_schemas (top-level entry) ---

def test_empty_list_passthrough():
    assert sanitize_tool_schemas([]) == []


def test_does_not_mutate_input():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    out = sanitize_tool_schemas(tools)
    out[0]["function"]["parameters"]["properties"]["x"] = {"type": "string"}
    assert "properties" not in tools[0]["function"]["parameters"]  # original untouched


def test_missing_parameters_gets_minimal_object():
    tools = [{"type": "function", "function": {"name": "f"}}]
    assert _params(sanitize_tool_schemas(tools)[0]) == {"type": "object", "properties": {}}


def test_non_dict_parameters_replaced():
    tools = [{"type": "function", "function": {"name": "f", "parameters": "object"}}]
    assert _params(sanitize_tool_schemas(tools)[0]) == {"type": "object", "properties": {}}


def test_top_level_forced_to_object_with_properties():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "string"}}}]
    top = _params(sanitize_tool_schemas(tools)[0])
    assert top["type"] == "object"
    assert top["properties"] == {}


def test_tool_without_function_returned_as_is():
    tools = [{"type": "function"}]
    assert sanitize_tool_schemas(tools) == [{"type": "function"}]


# --- _sanitize_node: bare-string schemas ---

def test_bare_string_object_becomes_object_with_properties():
    assert _sanitize_node("object", "p") == {"type": "object", "properties": {}}


def test_bare_string_scalar_becomes_typed_dict():
    assert _sanitize_node("string", "p") == {"type": "string"}


def test_non_schema_string_becomes_empty_object():
    assert _sanitize_node("garbage", "p") == {"type": "object", "properties": {}}


# --- _sanitize_node: array types ---

def test_array_type_with_null_collapses_to_single_and_keeps_hint():
    out = _sanitize_node({"type": ["string", "null"]}, "p")
    assert out["type"] == "string"
    assert out["nullable"] is True


def test_array_type_multiple_non_null_picks_first():
    out = _sanitize_node({"type": ["string", "integer"]}, "p")
    assert out["type"] == "string"


def test_array_type_all_null_becomes_object():
    out = _sanitize_node({"type": ["null"]}, "p")
    assert out["type"] == "object"


# --- _sanitize_node: object hygiene ---

def test_object_without_properties_gets_empty_properties():
    out = _sanitize_node({"type": "object"}, "p")
    assert out["properties"] == {}


def test_required_pruned_when_not_in_properties():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "ghost"]}
    out = _sanitize_node(schema, "p")
    assert out["required"] == ["a"]


def test_required_dropped_entirely_when_none_valid():
    schema = {"type": "object", "properties": {}, "required": ["ghost"]}
    out = _sanitize_node(schema, "p")
    assert "required" not in out


def test_enum_literals_not_treated_as_schemas():
    # "object"/"string" inside enum must survive verbatim, not become dicts.
    schema = {"type": "string", "enum": ["object", "string", "path"]}
    out = _sanitize_node(schema, "p")
    assert out["enum"] == ["object", "string", "path"]


def test_recurses_into_nested_properties():
    schema = {
        "type": "object",
        "properties": {"nested": {"type": ["integer", "null"]}},
    }
    out = _sanitize_node(schema, "p")
    assert out["properties"]["nested"]["type"] == "integer"
    assert out["properties"]["nested"]["nullable"] is True


def test_bool_additional_properties_preserved():
    out = _sanitize_node({"type": "object", "additionalProperties": False}, "p")
    assert out["additionalProperties"] is False


# --- strip_nullable_unions ---

def test_strip_nullable_anyof_collapses_to_non_null():
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}
    out = strip_nullable_unions(schema)
    assert out["type"] == "string"
    assert out["nullable"] is True
    assert out["default"] is None  # metadata carried over


def test_strip_nullable_oneof_collapses():
    schema = {"oneOf": [{"type": "integer"}, {"type": "null"}]}
    out = strip_nullable_unions(schema)
    assert out["type"] == "integer"


def test_strip_keeps_meaningful_union():
    # Two non-null branches → genuine union, leave intact.
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    out = strip_nullable_unions(schema)
    assert "anyOf" in out


def test_strip_can_drop_nullable_hint():
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    out = strip_nullable_unions(schema, keep_nullable_hint=False)
    assert "nullable" not in out


def test_strip_recurses_into_nested_properties():
    schema = {
        "type": "object",
        "properties": {
            "opt": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    }
    out = strip_nullable_unions(schema)
    assert out["properties"]["opt"]["type"] == "string"
