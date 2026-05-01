"""Stub for the Nous Tool Gateway.

Talaria does not include the managed gateway. Public API is kept as inert
no-ops so existing callers don't crash; nothing ever resolves as managed.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ManagedToolGatewayConfig:
    base_url: str = ""
    auth_token: str = ""
    vendor: str = ""


def auth_json_path():
    return None


def read_nous_access_token() -> Optional[str]:
    return None


def get_tool_gateway_scheme() -> str:
    return "https"


def build_vendor_gateway_url(_vendor: str) -> str:
    return ""


def resolve_managed_tool_gateway(*_args, **_kwargs) -> Optional[ManagedToolGatewayConfig]:
    return None


def is_managed_tool_gateway_ready(*_args, **_kwargs) -> bool:
    return False
