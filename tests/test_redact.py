"""Secret redaction (agent/redact)."""

import agent.redact as r


def test_mask_secret_hides_middle_keeps_ends():
    masked = r.mask_secret("sk-ant-1234567890abcdef")
    assert masked != "sk-ant-1234567890abcdef"
    assert masked.startswith("sk-a")
    assert "1234567890" not in masked


def test_redact_off_by_default_is_passthrough():
    # _REDACT_ENABLED is snapshotted False at import (opt-in only).
    s = "my key is sk-ant-ABCDEFGHIJKLMNOPQRST done"
    assert r.redact_sensitive_text(s) == s


def test_redact_force_masks_token_middle():
    s = "sk-ant-ABCDEFGHIJKLMNOPQRST"
    out = r.redact_sensitive_text(s, force=True)
    assert out != s
    assert "ABCDEFGHIJKLMNOP" not in out  # secret middle is gone


def test_redact_force_masks_aws_access_key():
    out = r.redact_sensitive_text("AKIAIOSFODNN7EXAMPLE", force=True)
    assert out != "AKIAIOSFODNN7EXAMPLE"
