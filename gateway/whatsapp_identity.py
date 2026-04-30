"""Compatibility shim for removed WhatsApp support."""

def normalize_whatsapp_identifier(value: str | None) -> str:
    return (value or "").strip()

def canonical_whatsapp_identifier(value: str | None) -> str:
    return normalize_whatsapp_identifier(value)
