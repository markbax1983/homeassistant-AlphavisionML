"""AlphaVision ML integratie (Generieke Interface)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import AlphaVisionError
from .const import CONF_KEY, DOMAIN
from .hub import AlphaVisionHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.ALARM_CONTROL_PANEL, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Zet een AlphaVision-config-entry op."""
    hub = AlphaVisionHub(
        hass, entry.entry_id, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_KEY]
    )

    try:
        await hub.async_start()
    except AlphaVisionError as ex:
        _LOGGER.error("Kon geen verbinding maken met het AlphaVision-paneel: %s", ex)
        raise ConfigEntryNotReady(str(ex)) from ex

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Ontlaad een AlphaVision-config-entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: AlphaVisionHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_stop()
    return unload_ok
