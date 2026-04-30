from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.runtime_config import runtime_config_value

logger = logging.getLogger(__name__)


class CallbackClient:
    def __init__(self):
        self.settings = get_settings()

    def send(self, callback_url: str, payload: dict[str, Any]) -> None:
        if not callback_url:
            return
        try:
            timeout = int(
                runtime_config_value(
                    "callback_timeout_seconds",
                    self.settings.callback_timeout_seconds,
                    self.settings,
                )
            )
            response = httpx.post(callback_url, json=payload, timeout=timeout)
            response.raise_for_status()
        except Exception as exc:
            logger.exception("callback failed: %s", exc)
