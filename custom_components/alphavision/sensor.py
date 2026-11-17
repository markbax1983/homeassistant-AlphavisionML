"""Sensor-entiteit voor de laatste gebeurtenis (bijv. wie in-/uitschakelde)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_UPDATE
from .hub import AlphaVisionHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: AlphaVisionHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [AlphaVisionLastEventSensor(hub, entry), AlphaVisionLastTestSensor(hub, entry)]
    )


class AlphaVisionLastEventSensor(SensorEntity):
    """Toont de omschrijving van de laatste gebeurtenis, met gebruiker/tijd als attributen.

    De periodieke verbindingstest van het paneel ('TRANS.1 TEST') komt hier niet
    in terecht; die heeft een eigen sensor.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:history"

    def __init__(self, hub: AlphaVisionHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._attr_name = "Laatste gebeurtenis"
        self._attr_unique_id = f"{entry.entry_id}_last_event"
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
    def native_value(self) -> str | None:
        if self._hub.last_event is None:
            return None
        return self._hub.last_event["description"]

    @property
    def extra_state_attributes(self) -> dict:
        if self._hub.last_event is None:
            return {}
        event = self._hub.last_event
        return {
            "user_number": event["user_number"],
            "user_name": event["user_name"],
            "timestamp": event["timestamp"],
            "result_code": event["result_code"],
        }

    @property
    def available(self) -> bool:
        return self._hub.available


class AlphaVisionLastTestSensor(SensorEntity):
    """Tijdstip van de laatste periodieke verbindingstest van het paneel.

    Het paneel stuurt elk uur een test ('TRANS.1 TEST'). Door het tijdstip als
    sensor te tonen is te zien (en te automatiseren) of die test nog binnenkomt.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:lan-check"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AlphaVisionHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._attr_name = "Laatste verbindingstest"
        self._attr_unique_id = f"{entry.entry_id}_last_test"
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
    def native_value(self) -> datetime | None:
        if self._hub.last_test is None:
            return None
        # Het paneel stuurt een tijd zonder tijdzone; die wordt als lokale tijd
        # van Home Assistant gelezen.
        parsed = dt_util.parse_datetime(self._hub.last_test["timestamp"])
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)

    @property
    def extra_state_attributes(self) -> dict:
        if self._hub.last_test is None:
            return {}
        return {"description": self._hub.last_test["description"]}

    @property
    def available(self) -> bool:
        return self._hub.available
