"""API error classification (agent/error_classifier) — drives retry/failover."""

from agent.error_classifier import classify_api_error


class _HTTPError(Exception):
    def __init__(self, status_code, msg=""):
        super().__init__(msg or f"HTTP {status_code}")
        self.status_code = status_code


def test_rate_limit_is_retryable():
    c = classify_api_error(_HTTPError(429, "rate limit exceeded"))
    assert c.status_code == 429
    assert c.retryable is True
    assert c.is_auth is False


def test_unauthorized_is_auth_not_retryable():
    c = classify_api_error(_HTTPError(401, "invalid api key"))
    assert c.is_auth is True
    assert c.retryable is False


def test_server_error_is_retryable():
    c = classify_api_error(_HTTPError(500, "internal error"))
    assert c.retryable is True
