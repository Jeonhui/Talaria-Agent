"""Pairing security (gateway/pairing.PairingStore).

Covers the access-control guarantees: code format, approve/revoke roundtrip,
invalid-code rejection, the per-platform pending cap, and lockout after
repeated failed approvals.
"""

import pytest

import gateway.pairing as p


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "PAIRING_DIR", tmp_path)
    return p.PairingStore()


def test_generated_code_format(store):
    code = store.generate_code("telegram", "u1", "Alice")
    assert code is not None
    assert len(code) == p.CODE_LENGTH
    assert code.isalnum()


def test_approve_roundtrip(store):
    code = store.generate_code("telegram", "u1", "Alice")
    assert store.is_approved("telegram", "u1") is False
    assert store.approve_code("telegram", code) == {"user_id": "u1", "user_name": "Alice"}
    assert store.is_approved("telegram", "u1") is True


def test_invalid_code_rejected(store):
    store.generate_code("telegram", "u1")
    assert store.approve_code("telegram", "WRONGXY") is None
    assert store.is_approved("telegram", "u1") is False


def test_per_platform_pending_cap(store):
    made = [store.generate_code("telegram", f"u{i}") for i in range(p.MAX_PENDING_PER_PLATFORM)]
    assert all(c is not None for c in made)
    # Exceeding the cap is refused.
    assert store.generate_code("telegram", "overflow") is None


def test_lockout_after_failed_attempts(store):
    store.generate_code("telegram", "u1")
    for _ in range(p.MAX_FAILED_ATTEMPTS):
        store.approve_code("telegram", "BADCODE")
    # Locked out: new code requests are refused.
    assert store.generate_code("telegram", "u2") is None


def test_revoke_removes_approval(store):
    code = store.generate_code("telegram", "u1", "Alice")
    store.approve_code("telegram", code)
    assert store.is_approved("telegram", "u1") is True
    assert store.revoke("telegram", "u1") is True
    assert store.is_approved("telegram", "u1") is False
