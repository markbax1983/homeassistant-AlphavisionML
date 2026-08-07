"""Binary sensors voor de AlphaVision ML zones."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATE, STATUS_NOT_INSTALLED, STATUS_OPEN, SYSTEM_SENSORS
from .hub import AlphaVisionHub


def _guess_device_class(name: str) -> BinarySensorDeviceClass | None:
    """Gok een passende device-class op basis van de zonenaam.

    De namen komen uit de configuratie van het paneel zelf (Nederlandse
    installateursconventies bij Alphatronics-systemen).
    """
    upper = name.upper()
    if upper.startswith("BRAND"):
        return BinarySensorDeviceClass.SMOKE
    if upper.startswith("BM"):
        return BinarySensorDeviceClass.MOTION
    if upper.startswith("DC"):
        return BinarySensorDeviceClass.DOOR
    if upper.startswith("SABOTAGE"):
        return BinarySensorDeviceClass.TAMPER
    if upper.startswith("STORING"):
        return BinarySensorDeviceClass.PROBLEM
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Maak een binary_sensor aan voor elke geinstalleerde zone."""
    hub: AlphaVisionHub = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone_nr, name in hub.zone_names.items():
        # Zones die volgens de eerste statusopvraag niet geinstalleerd zijn
        # (0x000F) krijgen bewust geen entiteit -- dat zijn lege/ongebruikte
        # zoneslots in de configuratie van het paneel.
        status = hub.zone_status.get(zone_nr)
        if status == STATUS_NOT_INSTALLED:
            continue
        entities.append(AlphaVisionZoneBinarySensor(hub, entry, zone_nr, name))

    for key, (name, device_class) in SYSTEM_SENSORS.items():
        entities.append(AlphaVisionSystemBinarySensor(hub, entry, key, name, device_class))

    async_add_entities(entities)


class AlphaVisionZoneBinarySensor(BinarySensorEntity):
    """Vertegenwoordigt 1 zone/ingang van het AlphaVision-paneel."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hub: AlphaVisionHub,
        entry: ConfigEntry,
        zone_nr: int,
        name: str,
    ) -> None:
        self._hub = hub
        self._zone_nr = zone_nr
        self._attr_name = name or f"Zone {zone_nr}"
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_nr}"
        self._attr_device_class = _guess_device_class(self._attr_name)
        self._attr_extra_state_attributes = {"zone_number": zone_nr, "zone_name": name}
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
    def is_on(self) -> bool | None:
        status = self._hub.zone_status.get(self._zone_nr)
        if status is None or status == STATUS_NOT_INSTALLED:
            return None
        return status == STATUS_OPEN

    @property
    def available(self) -> bool:
        return self._hub.available


class AlphaVisionSystemBinarySensor(BinarySensorEntity):
    """Vertegenwoordigt 1 systeemstatus-vlag (accu/netvoeding), samengevoegd
    over alle apparaten (paneel + uitbreidingsmodules)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hub: AlphaVisionHub,
        entry: ConfigEntry,
        key: str,
        name: str,
        device_class: str,
    ) -> None:
        self._hub = hub
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_system_{key}"
        self._attr_device_class = BinarySensorDeviceClass(device_class)
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
    def is_on(self) -> bool | None:
        return self._hub.device_status.get(self._key)

    @property
    def available(self) -> bool:
        return self._hub.available and bool(self._hub.device_status)
