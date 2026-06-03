"""execute_code sandbox credential-scrubbing guard (GHSA-rhgp-j443-p4rf).

Talaria-managed provider credentials must never be registerable as
sandbox env passthrough — not via skill frontmatter, not via config.
"""

from tools import env_passthrough as ep


def test_provider_credentials_are_flagged():
    assert ep._is_talaria_provider_credential("ANTHROPIC_API_KEY")
    assert ep._is_talaria_provider_credential("OPENAI_API_KEY")


def test_third_party_keys_not_flagged():
    assert not ep._is_talaria_provider_credential("NOTION_TOKEN")
    assert not ep._is_talaria_provider_credential("TENOR_API_KEY")


def test_register_refuses_provider_credential():
    ep.clear_env_passthrough()
    ep.register_env_passthrough(["ANTHROPIC_API_KEY", "MY_CUSTOM_VAR"])
    # provider key rejected, third-party var allowed through
    assert not ep.is_env_passthrough("ANTHROPIC_API_KEY")
    assert ep.is_env_passthrough("MY_CUSTOM_VAR")
    ep.clear_env_passthrough()
