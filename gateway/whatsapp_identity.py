"""Compatibility shim for removed WhatsApp support.

Talaria does not include the WhatsApp gateway. The public API is kept as
inert stubs so existing call sites (gateway/run.py, gateway/session.py)
keep working without pulling in the removed adapter.
"""


def normalize_whatsapp_identifier(value: str | None) -> str:
    return (value or "").strip()


def canonical_whatsapp_identifier(value: str | None) -> str:
    return normalize_whatsapp_identifier(value)


def expand_whatsapp_aliases(value: str | None) -> set[str]:
    """Return a single-element set with the canonical id (no alias expansion)."""
    canonical = canonical_whatsapp_identifier(value)
    return {canonical} if canonical else set()
