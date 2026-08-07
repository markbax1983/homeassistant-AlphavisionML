"""Config flow voor de AlphaVision ML integratie."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .client import AlphaVisionError, AsyncPanelSession
from .const import CONF_KEY, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_KEY): str,
    }
)


class AlphaVisionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow voor AlphaVision ML."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = AsyncPanelSession(
                user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_KEY]
            )
            try:
                await session.connect()
                await session.get_arrangement()
            except AlphaVisionError:
                _LOGGER.exception("Kon geen verbinding maken met het AlphaVision-paneel")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"AlphaVision ML ({user_input[CONF_HOST]})",
                    data=user_input,
                )
            finally:
                await session.close()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
