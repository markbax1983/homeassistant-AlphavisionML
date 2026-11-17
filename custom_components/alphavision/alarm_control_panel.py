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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import AlphaVisionError, AlphaVisionInvalidCodeError, AlphaVisionLockoutError
from .const import DOMAIN, SECTION_ARMED, SECTION_DISARMED, SIGNAL_UPDATE
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
        self._warned_unknown_status: set[int] = set()
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
        if status == SECTION_ARMED:
            return AlarmControlPanelState.ARMED_AWAY
        # Onbekende code: niet stilzwijgend als "ingeschakeld" tonen, maar als
        # onbekend, en eenmalig loggen zodat de betekenis uitgezocht kan worden
        # (bijv. een sectie in alarm).
        if status not in self._warned_unknown_status:
            self._warned_unknown_status.add(status)
            _LOGGER.warning(
                "Onbekende statuscode 0x%02X voor sectie %d (%s)",
                status, self._section_nr, self.name,
            )
        return None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        if not code:
            raise HomeAssistantError("Er is een code nodig om in te schakelen")
        try:
            accepted = await self._hub.async_arm(self._section_nr, code)
        except (AlphaVisionInvalidCodeError, AlphaVisionLockoutError) as ex:
            raise ServiceValidationError(str(ex)) from ex
        except AlphaVisionError as ex:
            raise HomeAssistantError(f"Inschakelen mislukt: {ex}") from ex
        if not accepted:
            raise HomeAssistantError("Inschakelen geweigerd door het paneel")

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        if not code:
            raise HomeAssistantError("Er is een code nodig om uit te schakelen")
        try:
            accepted = await self._hub.async_disarm(self._section_nr, code)
        except (AlphaVisionInvalidCodeError, AlphaVisionLockoutError) as ex:
            raise ServiceValidationError(str(ex)) from ex
        except AlphaVisionError as ex:
            raise HomeAssistantError(f"Uitschakelen mislukt: {ex}") from ex
        if not accepted:
            raise HomeAssistantError("Uitschakelen geweigerd door het paneel")

    @property
    def available(self) -> bool:
        return self._hub.available
