"""Binary sensor platform for Crestron NVX."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import crestron_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron NVX binary sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]
    api = data["api"]

    entities = []
    for device_name, coordinator in coordinators.items():
        device = api.get_device(device_name)
        entities.extend(
            [
                CrestronNVXVideoConnectedBinarySensor(coordinator, device),
                CrestronNVXNetworkConnectedBinarySensor(coordinator, device),
            ]
        )

    async_add_entities(entities)


class CrestronNVXBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base class for Crestron NVX binary sensors."""

    def __init__(self, coordinator, device):
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.device = device
        self._attr_device_info = crestron_device_info(device)


class CrestronNVXVideoConnectedBinarySensor(CrestronNVXBinarySensorBase):
    """Binary sensor for HDMI source/sink connection state."""

    def __init__(self, coordinator, device):
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        label = "Sink Connected" if device.is_receiver else "Signal Detected"
        self._attr_name = f"{device.name} {label}"
        self._attr_unique_id = f"{device.host}_video_connected_binary"
        self._attr_icon = "mdi:video-input-hdmi"

    @property
    def is_on(self) -> bool | None:
        """Return whether HDMI sync/sink is present."""
        video = (self.coordinator.data or {}).get("video")
        if video is None:
            return None
        return bool(video.get("connected"))


class CrestronNVXNetworkConnectedBinarySensor(CrestronNVXBinarySensorBase):
    """Binary sensor for primary Ethernet link state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, device):
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self._attr_name = f"{device.name} Network Connected"
        self._attr_unique_id = f"{device.host}_network_connected_binary"

    @property
    def is_on(self) -> bool | None:
        """Return whether the primary Ethernet adapter is linked."""
        ethernet = (self.coordinator.data or {}).get("ethernet")
        if ethernet is None:
            return None
        return bool(ethernet.get("connected"))

    @property
    def extra_state_attributes(self):
        """Return network details from the latest poll."""
        ethernet = (self.coordinator.data or {}).get("ethernet") or {}
        return {
            "ip_address": ethernet.get("ip_address"),
            "mac_address": ethernet.get("mac_address"),
        }
