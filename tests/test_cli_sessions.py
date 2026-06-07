"""Coverage for the pure helpers in ``talaria_cli/sessions.py``.

``_relative_time`` and ``_coalesce_session_name_args`` are the two
non-curses helpers the module exports.  Both shape the ``talaria sessions
browse`` and ``talaria -c`` user experience, so silently changing their
output (or their argv-rewriting rules) would change behavior that users
rely on day-to-day.
"""

import time as real_time

import pytest

from talaria_cli.sessions import _coalesce_session_name_args, _relative_time

# ── _relative_time ────────────────────────────────────────────────────────


def test_relative_time_empty_input():
    assert _relative_time(None) == "?"
    assert _relative_time(0) == "?"
    assert _relative_time("") == "?"


def test_relative_time_just_now():
    assert _relative_time(real_time.time() - 5) == "just now"


def test_relative_time_minutes_ago():
    out = _relative_time(real_time.time() - 5 * 60)
    assert out == "5m ago"


def test_relative_time_hours_ago():
    out = _relative_time(real_time.time() - 3 * 3600)
    assert out == "3h ago"


def test_relative_time_yesterday_window():
    """24h < delta < 48h reads as 'yesterday'."""
    out = _relative_time(real_time.time() - 30 * 3600)
    assert out == "yesterday"


def test_relative_time_days_ago():
    out = _relative_time(real_time.time() - 3 * 86400)
    assert out == "3d ago"


def test_relative_time_falls_back_to_absolute_date_after_a_week():
    """Beyond a week, the function emits an absolute YYYY-MM-DD string
    instead of "N weeks ago" — anchors very old sessions to a calendar
    date that's easier to scan."""
    out = _relative_time(real_time.time() - 30 * 86400)
    # We can't assert the exact date without time-freezing, but we can
    # lock in the shape.
    assert len(out) == 10
    assert out.count("-") == 2


# ── _coalesce_session_name_args ────────────────────────────────────────────


def test_coalesce_no_session_flag_is_passthrough():
    assert _coalesce_session_name_args(["status"]) == ["status"]
    assert _coalesce_session_name_args(["-q", "hi"]) == ["-q", "hi"]


def test_coalesce_joins_unquoted_multi_word_name():
    """The canonical case: ``talaria -c Pokemon Agent Dev`` becomes
    ``['-c', 'Pokemon Agent Dev']``."""
    out = _coalesce_session_name_args(["-c", "Pokemon", "Agent", "Dev"])
    assert out == ["-c", "Pokemon Agent Dev"]


def test_coalesce_stops_at_next_flag():
    """Collection halts at the next ``-*`` token so flags after the name
    keep working."""
    out = _coalesce_session_name_args(["-c", "Pokemon", "Agent", "-q", "hi"])
    assert out == ["-c", "Pokemon Agent", "-q", "hi"]


def test_coalesce_stops_at_known_subcommand():
    """A known subcommand mid-stream terminates the name so things like
    ``-c foo status`` don't swallow the subcommand."""
    out = _coalesce_session_name_args(["-c", "foo", "status"])
    assert out == ["-c", "foo", "status"]


def test_coalesce_handles_long_form_flags():
    assert _coalesce_session_name_args(["--continue", "a", "b"]) == ["--continue", "a b"]
    assert _coalesce_session_name_args(["--resume", "a", "b"]) == ["--resume", "a b"]


def test_coalesce_session_flag_with_no_trailing_tokens():
    """``-c`` alone (no name) must not produce ``['-c', '']``."""
    out = _coalesce_session_name_args(["-c"])
    assert out == ["-c"]


def test_coalesce_session_flag_followed_by_flag_keeps_both():
    """``-c -q "..."`` (no name) must leave the flags intact for
    argparse to error on later."""
    out = _coalesce_session_name_args(["-c", "-q", "hi"])
    assert out == ["-c", "-q", "hi"]


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], []),
        (["-c", "single"], ["-c", "single"]),
        (["-r", "two", "words"], ["-r", "two words"]),
        # Two consecutive session flags shouldn't cross-pollute.
        (["-c", "A", "B", "-r", "X", "Y"], ["-c", "A B", "-r", "X Y"]),
    ],
)
def test_coalesce_parametrized_cases(argv, expected):
    assert _coalesce_session_name_args(argv) == expected
