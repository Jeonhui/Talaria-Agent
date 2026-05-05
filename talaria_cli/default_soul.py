"""Default SOUL.md template seeded into TALARIA_HOME on first run.

The body is sourced from ``branding.build_agent_identity`` so a fork only
edits ``branding.py`` to retheme the seeded identity.
"""

from talaria_cli.branding import DEFAULT_AGENT_IDENTITY, build_agent_identity


def render_default_soul(user_name: str = "") -> str:
    """Render the SOUL.md text, optionally addressing a known user."""
    return build_agent_identity(user_name)


# Backwards-compatible constant — used by callers that don't need the
# user-name variant.
DEFAULT_SOUL_MD = DEFAULT_AGENT_IDENTITY
