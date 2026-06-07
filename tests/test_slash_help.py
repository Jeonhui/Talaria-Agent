"""Coverage for the extracted ``/help`` and ``/commands`` handlers.

``gateway/slash_help.py`` is the pilot A2 extraction (see
``docs/REFACTOR-ROADMAP.md``).  Locking the rendering behavior in place
here lets future slash-command extractions follow the same pattern with
confidence that the help / paginated-commands surface will not silently
regress.
"""

import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.slash_help import handle_commands, handle_help


def _event(text: str = "/help", *, platform: Platform = Platform.TELEGRAM) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=platform,
            chat_id="100",
            chat_type="dm",
            user_id="42",
        ),
    )


def _stub_help_lines(monkeypatch, lines):
    import talaria_cli.commands as cmds

    monkeypatch.setattr(cmds, "gateway_help_lines", lambda: lines)


def _stub_skill_commands(monkeypatch, skill_cmds):
    import agent.skill_commands as sc

    monkeypatch.setattr(sc, "get_skill_commands", lambda: skill_cmds)


# ── /help ──────────────────────────────────────────────────────────────────


def test_help_lists_builtin_commands(monkeypatch):
    _stub_help_lines(monkeypatch, ["`/new` — start fresh", "`/reset` — clear session"])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_help(_event()))

    assert "📖 **Talaria Commands**" in out
    assert "`/new` — start fresh" in out
    assert "`/reset` — clear session" in out
    assert "Skill Commands" not in out


def test_help_appends_first_ten_skill_commands(monkeypatch):
    _stub_help_lines(monkeypatch, [])
    skills = {f"/cmd{i}": {"description": f"desc {i}"} for i in range(12)}
    _stub_skill_commands(monkeypatch, skills)

    out = asyncio.run(handle_help(_event()))

    assert "⚡ **Skill Commands** (12 active)" in out
    # First 10 shown, alphabetical
    sorted_cmds = sorted(skills)
    for cmd in sorted_cmds[:10]:
        assert f"`{cmd}` — desc" in out
    # 11th and 12th skipped, replaced with an overflow hint pointing to /commands
    assert sorted_cmds[10] not in out
    assert "... and 2 more" in out
    assert "Use `/commands`" in out


def test_help_skill_load_failure_does_not_crash(monkeypatch):
    _stub_help_lines(monkeypatch, ["entry"])

    import agent.skill_commands as sc

    def _boom():
        raise RuntimeError("registry not ready")

    monkeypatch.setattr(sc, "get_skill_commands", _boom)

    out = asyncio.run(handle_help(_event()))

    assert "entry" in out  # built-in section still rendered
    assert "Skill Commands" not in out


# ── /commands ──────────────────────────────────────────────────────────────


def test_commands_pagination_telegram_page_size_is_15(monkeypatch):
    """Telegram's tighter message limits → 15 entries / page."""
    _stub_help_lines(monkeypatch, [f"entry {i}" for i in range(40)])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text="/commands")))

    assert "**Commands** (40 total, page 1/3)" in out
    assert "entry 0" in out and "entry 14" in out
    assert "entry 15" not in out  # page 2 only
    assert "next → `/commands 2`" in out


def test_commands_pagination_other_platforms_use_20(monkeypatch):
    _stub_help_lines(monkeypatch, [f"e{i}" for i in range(40)])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text="/commands", platform=Platform.DISCORD)))

    assert "**Commands** (40 total, page 1/2)" in out
    assert "e19" in out
    assert "e20" not in out


def test_commands_invalid_page_argument_returns_usage_line(monkeypatch):
    _stub_help_lines(monkeypatch, ["e1"])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text="/commands notanint")))

    assert out == "Usage: `/commands [page]`"


def test_commands_out_of_range_page_clamps_and_notes(monkeypatch):
    _stub_help_lines(monkeypatch, [f"e{i}" for i in range(5)])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text="/commands 99")))

    assert "page 1/1" in out
    assert "Requested page 99 was out of range, showing page 1." in out


def test_commands_includes_skill_section(monkeypatch):
    _stub_help_lines(monkeypatch, ["built-in"])
    _stub_skill_commands(monkeypatch, {"/skill1": {"description": "do thing"}})

    out = asyncio.run(handle_commands(_event(text="/commands")))

    assert "built-in" in out
    assert "⚡ **Skill Commands**" in out
    assert "`/skill1` — do thing" in out


def test_commands_empty_returns_friendly_message(monkeypatch):
    _stub_help_lines(monkeypatch, [])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text="/commands")))

    assert out == "No commands available."


@pytest.mark.parametrize(
    "page_arg,expected_page",
    [("", 1), ("1", 1), ("2", 2), ("3", 3)],
)
def test_commands_explicit_page_argument(monkeypatch, page_arg, expected_page):
    _stub_help_lines(monkeypatch, [f"e{i}" for i in range(50)])
    _stub_skill_commands(monkeypatch, {})

    out = asyncio.run(handle_commands(_event(text=f"/commands {page_arg}".strip())))

    assert f"page {expected_page}" in out
