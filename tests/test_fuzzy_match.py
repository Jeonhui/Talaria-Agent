"""Tests for the fuzzy find-and-replace engine used by the edit tool."""

from __future__ import annotations

from tools.fuzzy_match import (
    find_closest_lines,
    format_no_match_hint,
    fuzzy_find_and_replace,
)

# --- guard conditions ---

def test_empty_old_string_errors():
    new, count, strat, err = fuzzy_find_and_replace("abc", "", "x")
    assert count == 0 and strat is None
    assert err == "old_string cannot be empty"


def test_identical_strings_error():
    new, count, strat, err = fuzzy_find_and_replace("abc", "a", "a")
    assert count == 0
    assert err == "old_string and new_string are identical"


def test_no_match_error():
    new, count, strat, err = fuzzy_find_and_replace("hello world", "xyz", "q")
    assert count == 0 and new == "hello world"
    assert err is not None and err.startswith("Could not find")


# --- exact strategy ---

def test_exact_single_replace():
    new, count, strat, err = fuzzy_find_and_replace("a b c", "b", "B")
    assert new == "a B c"
    assert count == 1
    assert strat == "exact"
    assert err is None


def test_exact_multiple_without_replace_all_is_ambiguous():
    new, count, strat, err = fuzzy_find_and_replace("x x x", "x", "y")
    assert count == 0
    assert err is not None and "Found 3 matches" in err
    assert new == "x x x"  # unchanged


def test_exact_multiple_with_replace_all():
    new, count, strat, err = fuzzy_find_and_replace("x x x", "x", "y", replace_all=True)
    assert new == "y y y"
    assert count == 3
    assert err is None


# --- fuzzy strategies (assert success, not exact strategy label) ---

def test_line_trimmed_match_when_indentation_differs():
    content = "def f():\n        return 1\n"   # 8-space indent
    old = "def f():\n    return 1"               # 4-space indent
    new, count, strat, err = fuzzy_find_and_replace(content, old, "def f():\n    return 2")
    assert err is None
    assert count >= 1
    assert strat != "exact"  # matched via a normalization strategy
    assert "return 2" in new


def test_trailing_whitespace_tolerated():
    content = "alpha   \nbeta\n"   # trailing spaces after alpha
    new, count, strat, err = fuzzy_find_and_replace(content, "alpha\nbeta", "ALPHA\nbeta")
    assert err is None
    assert count >= 1
    assert "ALPHA" in new


# --- find_closest_lines ---

def test_find_closest_lines_returns_snippet_for_near_match():
    content = "import os\nimport sys\nfrom pathlib import Path\n"
    hint = find_closest_lines("from pathlib import Pth", content)
    assert "pathlib" in hint
    assert "|" in hint  # formatted with line numbers


def test_find_closest_lines_empty_for_no_input():
    assert find_closest_lines("", "abc") == ""
    assert find_closest_lines("abc", "") == ""


def test_find_closest_lines_empty_when_nothing_similar():
    assert find_closest_lines("zzzzzzzz", "completely different text here") == ""


# --- format_no_match_hint (gating) ---

def test_hint_only_for_not_found_errors():
    content = "import sys\nimport os\n"
    hint = format_no_match_hint("Could not find a match for old_string in the file",
                                0, "import sustem", content)
    assert "Did you mean" in hint or hint  # non-empty snippet appended


def test_hint_suppressed_for_ambiguous_match_error():
    # match_count semantics: ambiguous/escape/identical all use count 0 but a
    # "did you mean" snippet would mislead — gated by the error prefix.
    assert format_no_match_hint("Found 3 matches for old_string.", 0, "x", "x x x") == ""


def test_hint_suppressed_when_match_count_nonzero():
    assert format_no_match_hint("Could not find", 2, "x", "abc") == ""
