"""Self-contained ``/help`` and ``/commands`` slash-command handlers.

Pilot extraction validating the A2 pattern in
``docs/REFACTOR-ROADMAP.md``: lift slash-command handlers off the
``GatewayRunner`` class so they can be unit-tested without partial
construction tricks.

These two handlers are the obvious first move because they touch zero
``GatewayRunner`` state — both are pure functions of the incoming
``MessageEvent``.  The runner methods are kept as one-line delegators
so the existing call sites in ``GatewayRunner._handle_message`` are
unchanged.
"""

from __future__ import annotations

from gateway.config import Platform
from gateway.platforms.base import MessageEvent


async def handle_help(event: MessageEvent) -> str:
    """``/help`` — list the built-in gateway commands plus the first few
    active skill commands.  See :func:`handle_commands` for the full
    paginated view."""
    from talaria_cli.commands import gateway_help_lines

    lines = [
        "📖 **Talaria Commands**\n",
        *gateway_help_lines(),
    ]
    try:
        from agent.skill_commands import get_skill_commands

        skill_cmds = get_skill_commands()
        if skill_cmds:
            lines.append(f"\n⚡ **Skill Commands** ({len(skill_cmds)} active):")
            sorted_cmds = sorted(skill_cmds)
            for cmd in sorted_cmds[:10]:
                lines.append(f"`{cmd}` — {skill_cmds[cmd]['description']}")
            if len(sorted_cmds) > 10:
                lines.append(
                    f"\n... and {len(sorted_cmds) - 10} more. "
                    "Use `/commands` for the full paginated list."
                )
    except Exception:
        pass
    return "\n".join(lines)


async def handle_commands(event: MessageEvent) -> str:
    """``/commands [page]`` — paginated list of every command and skill.

    Page size is platform-aware: Telegram caps at 15 entries / page (its
    message size limit makes longer pages risky), other platforms use 20.
    """
    from talaria_cli.commands import gateway_help_lines

    raw_args = event.get_command_args().strip()
    if raw_args:
        try:
            requested_page = int(raw_args)
        except ValueError:
            return "Usage: `/commands [page]`"
    else:
        requested_page = 1

    # Built-in commands first, then any active skill commands.
    entries = list(gateway_help_lines())
    try:
        from agent.skill_commands import get_skill_commands

        skill_cmds = get_skill_commands()
        if skill_cmds:
            entries.append("")
            entries.append("⚡ **Skill Commands**:")
            for cmd in sorted(skill_cmds):
                desc = skill_cmds[cmd].get("description", "").strip() or "Skill command"
                entries.append(f"`{cmd}` — {desc}")
    except Exception:
        pass

    if not entries:
        return "No commands available."

    page_size = 15 if event.source.platform == Platform.TELEGRAM else 20
    total_pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = max(1, min(requested_page, total_pages))
    start = (page - 1) * page_size
    page_entries = entries[start:start + page_size]

    lines = [
        f"📚 **Commands** ({len(entries)} total, page {page}/{total_pages})",
        "",
        *page_entries,
    ]
    if total_pages > 1:
        nav_parts = []
        if page > 1:
            nav_parts.append(f"`/commands {page - 1}` ← prev")
        if page < total_pages:
            nav_parts.append(f"next → `/commands {page + 1}`")
        lines.extend(["", " | ".join(nav_parts)])
    if page != requested_page:
        lines.append(
            f"_(Requested page {requested_page} was out of range, "
            f"showing page {page}.)_"
        )
    return "\n".join(lines)
