"""Config flow voor de AlphaVision ML integratie."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .client import AlphaVisionError, AsyncPanelSession
from .const import CONF_KEY, DEFAULT_PORT, DOMAIN, KEY_LENGTHS, PENDING_SESSIONS

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
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"

            # Eerst controleren of dit paneel al is geconfigureerd, voordat er
            # verbonden wordt: een tweede verbinding naar een paneel dat al in
            # gebruik is kan de bestaande sessie verstoren.
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # AES accepteert alleen sleutels van 16, 24 of 32 bytes. Controleer
            # dat vooraf, zodat een verkeerde sleutel een nette foutmelding
            # geeft in plaats van een onverwachte fout in de flow.
            if len(user_input[CONF_KEY].encode("utf-8")) not in KEY_LENGTHS:
                errors["base"] = "invalid_key"
            else:
                session = AsyncPanelSession(
                    user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_KEY]
                )
                handed_off = False
                try:
                    await session.connect()
                    await session.get_arrangement()
                except AlphaVisionError:
                    _LOGGER.exception("Kon geen verbinding maken met het AlphaVision-paneel")
                    errors["base"] = "cannot_connect"
                except ValueError:
                    _LOGGER.exception("Ongeldige encryptiesleutel voor het AlphaVision-paneel")
                    errors["base"] = "invalid_key"
                else:
                    # De gevalideerde, open sessie wordt aan de hub doorgegeven
                    # i.p.v. gesloten: opnieuw verbinden direct na een afsluiting
                    # is precies wat het paneel slecht verdraagt.
                    pending = self.hass.data.setdefault(DOMAIN, {}).setdefault(
                        PENDING_SESSIONS, {}
                    )
                    stale = pending.pop(unique_id, None)
                    if stale is not None:
                        await stale.close()
                    pending[unique_id] = session
                    handed_off = True
                    return self.async_create_entry(
                        title=f"AlphaVision ML ({user_input[CONF_HOST]})",
                        data=user_input,
                    )
                finally:
                    if not handed_off:
                        await session.close()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
