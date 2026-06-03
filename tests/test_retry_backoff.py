"""Backoff schedule (agent/retry_utils.jittered_backoff)."""

from agent.retry_utils import jittered_backoff


def test_backoff_is_positive():
    assert jittered_backoff(0) > 0


def test_backoff_respects_max_delay():
    # With jitter_ratio=0.5 the output never exceeds max_delay * 1.5.
    for _ in range(50):
        d = jittered_backoff(50, base_delay=5, max_delay=30)
        assert d <= 30 * 1.5 + 1e-6


def test_backoff_grows_with_attempt_on_average():
    early = sum(jittered_backoff(0, base_delay=5, max_delay=120) for _ in range(20)) / 20
    later = sum(jittered_backoff(4, base_delay=5, max_delay=120) for _ in range(20)) / 20
    assert later > early
