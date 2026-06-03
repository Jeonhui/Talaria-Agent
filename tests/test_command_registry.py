"""Slash-command registry invariants.

Locks in the post-REPL cleanup: the interactive terminal chat (cli.py) was
removed, so any ``cli_only`` command without a ``gateway_config_gate`` is
unreachable and must not exist in the registry.
"""

from talaria_cli import commands as c


def test_no_unreachable_cli_only_commands():
    dead = [
        cd.name
        for cd in c.COMMAND_REGISTRY
        if cd.cli_only and not cd.gateway_config_gate
    ]
    assert dead == [], f"unreachable cli_only commands present: {dead}"


def test_resolve_command_name_slash_and_alias():
    assert c.resolve_command("new") is not None
    assert c.resolve_command("/new") is not None
    # 'reset' is an alias of 'new'
    assert c.resolve_command("reset") is c.resolve_command("new")


def test_removed_repl_commands_are_gone():
    for name in ("clear", "skin", "snapshot", "quit", "copy", "paste", "browser"):
        assert c.resolve_command(name) is None, f"{name} should have been removed"


def test_gated_cli_only_command_survives():
    # 'verbose' is cli_only but gated into the gateway, so it stays.
    assert c.resolve_command("verbose") is not None


def test_registry_consumers_do_not_raise():
    # These feed gateway help, the Telegram menu, and Slack routing.
    c.gateway_help_lines()
    c.telegram_bot_commands()
    c.slack_subcommand_map()


def test_gateway_help_excludes_dead_repl_commands():
    text = "\n".join(c.gateway_help_lines())
    for name in ("/clear", "/skin", "/snapshot", "/copy"):
        assert name not in text
