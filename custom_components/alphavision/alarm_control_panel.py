"""Alarm control panel entiteiten voor de AlphaVision ML secties.

Elke sectie van het paneel wordt een eigen alarm_control_panel-entiteit, met
echte in-/uitschakelbediening via de Generieke Interface.

De statuscode-betekenis is empirisch hard bevestigd met een gerichte test
(arm/disarm-commando en het paneel-eigen diagnoselog naast elkaar gelegd):
  - 0x02 = sectie uitgeschakeld
  - 0x01 = sectie ingeschakeld
  - resultaatcode 0x03 op een arm/disarm-verzoek = geweigerd door het paneel
Er bleek geen aparte "niet geconfigureerd"-code te bestaan (dat was een
eerdere, onjuiste aanname) -- secties die niet in de sectie-indeling van het
paneel voorkomen worden simpelweg genegeerd.
"""

from __future__ import annotations

import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SECTION_DISARMED, SIGNAL_UPDATE
from .hub import AlphaVisionHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Maak een alarm_control_panel aan voor elke sectie."""
    hub: AlphaVisionHub = hass.data[DOMAIN][entry.entry_id]

    entities = [
        AlphaVisionAlarmPanel(hub, entry, section_nr, name)
        for section_nr, name in hub.section_names.items()
    ]
    async_add_entities(entities)


class AlphaVisionAlarmPanel(AlarmControlPanelEntity):
    """Vertegenwoordigt 1 sectie als in-/uitschakelbaar alarmpaneel."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(
        self,
        hub: AlphaVisionHub,
        entry: ConfigEntry,
        section_nr: int,
        name: str,
    ) -> None:
        self._hub = hub
        self._section_nr = section_nr
        self._attr_name = name or f"Sectie {section_nr}"
        self._attr_unique_id = f"{entry.entry_id}_alarm_section_{section_nr}"
        self._attr_extra_state_attributes = {
            "section_number": section_nr,
            "section_name": name,
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Alphatronics",
            model="AlphaVision ML",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        status = self._hub.section_status.get(self._section_nr)
        if status is None:
            return None
        if status == SECTION_DISARMED:
            return AlarmControlPanelState.DISARMED
        return AlarmControlPanelState.ARMED_AWAY

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        if not code:
            raise HomeAssistantError("Er is een code nodig om in te schakelen")
        accepted = await self._hub.async_arm(self._section_nr, code)
        if not accepted:
            raise HomeAssistantError("Inschakelen geweigerd door het paneel")

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        if not code:
            raise HomeAssistantError("Er is een code nodig om uit te schakelen")
        accepted = await self._hub.async_disarm(self._section_nr, code)
        if not accepted:
            raise HomeAssistantError("Uitschakelen geweigerd door het paneel")

    @property
    def available(self) -> bool:
        return self._hub.available
