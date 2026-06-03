"""Agent-loop tool-batch parallelization safety (run_agent).

These pure decision functions gate whether a batch of tool calls may run
concurrently — a wrong 'yes' could race file writes or run an interactive tool
in parallel, so the logic is worth locking down.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import run_agent as R


def _tc(name, **args):
    """Build a fake tool_call with .function.name / .function.arguments (JSON)."""
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))


# ── _is_destructive_command ────────────────────────────────────────────────

def test_destructive_detects_mutating_commands():
    assert R._is_destructive_command("rm -rf foo")
    assert R._is_destructive_command("mv a b")
    assert R._is_destructive_command("sed -i s/a/b/ f")
    assert R._is_destructive_command("echo x > file")  # overwrite redirect


def test_destructive_allows_readonly_commands():
    assert not R._is_destructive_command("ls -la")
    assert not R._is_destructive_command("cat file")
    assert not R._is_destructive_command("echo x >> file")  # append, not overwrite
    assert not R._is_destructive_command("")


# ── _should_parallelize_tool_batch ─────────────────────────────────────────

def test_single_call_is_not_parallelized():
    assert R._should_parallelize_tool_batch([_tc("read_file", path="/a")]) is False


def test_two_readonly_tools_parallelize():
    batch = [_tc("web_search", q="a"), _tc("web_search", q="b")]
    assert R._should_parallelize_tool_batch(batch) is True


def test_path_scoped_distinct_paths_parallelize():
    batch = [_tc("read_file", path="/a/x"), _tc("read_file", path="/b/y")]
    assert R._should_parallelize_tool_batch(batch) is True


def test_path_scoped_overlapping_paths_serialize():
    batch = [_tc("read_file", path="/a/x"), _tc("read_file", path="/a/x")]
    assert R._should_parallelize_tool_batch(batch) is False


def test_interactive_tool_forces_serial():
    batch = [_tc("clarify", q="?"), _tc("web_search", q="b")]
    assert R._should_parallelize_tool_batch(batch) is False


def test_unknown_tool_forces_serial():
    batch = [_tc("terminal", cmd="ls"), _tc("web_search", q="b")]
    assert R._should_parallelize_tool_batch(batch) is False


def test_unparseable_args_force_serial():
    bad = SimpleNamespace(function=SimpleNamespace(name="read_file", arguments="{not json"))
    assert R._should_parallelize_tool_batch([bad, _tc("web_search", q="b")]) is False


# ── path helpers ────────────────────────────────────────────────────────────

def test_extract_scope_path_abspath():
    p = R._extract_parallel_scope_path("read_file", {"path": "/tmp/x"})
    assert p == Path("/tmp/x")
    assert R._extract_parallel_scope_path("web_search", {"path": "/tmp/x"}) is None
    assert R._extract_parallel_scope_path("read_file", {}) is None


def test_paths_overlap():
    assert R._paths_overlap(Path("/a/b"), Path("/a/b/c")) is True
    assert R._paths_overlap(Path("/a/b"), Path("/a/c")) is False
