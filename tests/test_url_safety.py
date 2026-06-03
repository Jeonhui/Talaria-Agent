"""SSRF guard (tools/url_safety.is_safe_url).

Uses IP literals (not hostnames) so the tests don't depend on DNS / network.
"""

from tools.url_safety import is_safe_url


def test_blocks_loopback():
    assert not is_safe_url("http://127.0.0.1/")
    assert not is_safe_url("http://[::1]/")


def test_blocks_cloud_metadata_endpoint():
    assert not is_safe_url("http://169.254.169.254/latest/meta-data/")


def test_blocks_private_ranges():
    for url in (
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
    ):
        assert not is_safe_url(url), url


def test_blocks_non_http_schemes():
    assert not is_safe_url("file:///etc/passwd")
    assert not is_safe_url("ftp://198.51.100.1/")


def test_allows_public_ip():
    # 8.8.8.8 and 1.1.1.1 are public; IP literals avoid a DNS lookup.
    assert is_safe_url("https://8.8.8.8/")
    assert is_safe_url("https://1.1.1.1/v1/models")
