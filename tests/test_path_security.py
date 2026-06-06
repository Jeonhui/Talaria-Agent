"""Tests for shared path-traversal guards used by tool implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.path_security import has_traversal_component, validate_within_dir

# --- has_traversal_component ---

@pytest.mark.parametrize(
    "path_str",
    ["../etc/passwd", "a/../../b", "foo/..", "../"],
)
def test_traversal_detected(path_str):
    assert has_traversal_component(path_str) is True


@pytest.mark.parametrize(
    "path_str",
    ["a/b/c", "foo.txt", "dir/sub/file", "./relative", "..hidden/file"],
)
def test_no_traversal(path_str):
    # Note: "..hidden" is a normal name, not a ".." component.
    assert has_traversal_component(path_str) is False


# --- validate_within_dir ---

def test_path_inside_root_is_allowed(tmp_path):
    inside = tmp_path / "sub" / "file.txt"
    assert validate_within_dir(inside, tmp_path) is None


def test_path_equal_to_root_is_allowed(tmp_path):
    assert validate_within_dir(tmp_path, tmp_path) is None


def test_path_outside_root_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "secret"
    err = validate_within_dir(outside, root)
    assert err is not None
    assert "escapes allowed directory" in err


def test_dotdot_escape_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    escape = root / ".." / "secret.txt"
    err = validate_within_dir(escape, root)
    assert err is not None


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    secret_dir = tmp_path / "outside"
    secret_dir.mkdir()
    link = root / "link"
    try:
        link.symlink_to(secret_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    # resolve() follows the symlink out of root -> must be rejected.
    err = validate_within_dir(link / "file.txt", root)
    assert err is not None
