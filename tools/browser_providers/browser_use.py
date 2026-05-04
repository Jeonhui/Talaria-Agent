"""Browser Use cloud browser provider."""

import logging
import os
import uuid
from typing import Any, Dict, Optional

import requests

from tools.browser_providers.base import CloudBrowserProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.browser-use.com/api/v3"


class BrowserUseProvider(CloudBrowserProvider):
    """Browser Use (https://browser-use.com) cloud browser backend."""

    def provider_name(self) -> str:
        return "Browser Use"

    def is_configured(self) -> bool:
        return self._get_config_or_none() is not None

    def _get_config_or_none(self) -> Optional[Dict[str, Any]]:
        api_key = os.environ.get("BROWSER_USE_API_KEY")
        if not api_key:
            return None
        return {
            "api_key": api_key,
            "base_url": _BASE_URL,
        }

    def _get_config(self) -> Dict[str, Any]:
        config = self._get_config_or_none()
        if config is None:
            raise ValueError(
                "Browser Use requires a direct BROWSER_USE_API_KEY credential."
            )
        return config

    def _headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Browser-Use-API-Key": config["api_key"],
        }

    def create_session(self, task_id: str) -> Dict[str, object]:
        config = self._get_config()
        headers = self._headers(config)

        response = requests.post(
            f"{config['base_url']}/browsers",
            headers=headers,
            json={},
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Browser Use session: "
                f"{response.status_code} {response.text}"
            )

        session_data = response.json()
        session_name = f"talaria_{task_id}_{uuid.uuid4().hex[:8]}"

        logger.info("Created Browser Use session %s", session_name)

        cdp_url = session_data.get("cdpUrl") or session_data.get("connectUrl") or ""

        return {
            "session_name": session_name,
            "bb_session_id": session_data["id"],
            "cdp_url": cdp_url,
            "features": {"browser_use": True},
        }

    def close_session(self, session_id: str) -> bool:
        try:
            config = self._get_config()
        except ValueError:
            logger.warning("Cannot close Browser Use session %s — missing credentials", session_id)
            return False

        try:
            response = requests.patch(
                f"{config['base_url']}/browsers/{session_id}",
                headers=self._headers(config),
                json={"action": "stop"},
                timeout=10,
            )
            if response.status_code in (200, 201, 204):
                logger.debug("Successfully closed Browser Use session %s", session_id)
                return True
            else:
                logger.warning(
                    "Failed to close Browser Use session %s: HTTP %s - %s",
                    session_id,
                    response.status_code,
                    response.text[:200],
                )
                return False
        except Exception as e:
            logger.error("Exception closing Browser Use session %s: %s", session_id, e)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        config = self._get_config_or_none()
        if config is None:
            logger.warning("Cannot emergency-cleanup Browser Use session %s — missing credentials", session_id)
            return
        try:
            requests.patch(
                f"{config['base_url']}/browsers/{session_id}",
                headers=self._headers(config),
                json={"action": "stop"},
                timeout=5,
            )
        except Exception as e:
            logger.debug("Emergency cleanup failed for Browser Use session %s: %s", session_id, e)
