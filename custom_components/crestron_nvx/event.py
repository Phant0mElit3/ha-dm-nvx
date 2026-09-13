"""Event platform for Crestron NVX - listens for CEC remote presses.

A transmitter's HDMI input passively captures CEC traffic the connected
source sends toward the display (e.g. an Apple TV's remote, when its
"Volume Control" setting is set to HDMI-CEC, sends volume/mute/power
commands over CEC rather than only over Bluetooth to the Apple TV box).
This entity listens for that traffic via /Device/Longpoll and turns each
recognized command into a Home Assistant event an automation can trigger
on. Nothing is ever sent back to the device from here.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CEC_EVENT_TYPES, DOMAIN
from .crestron_nvx_api import decode_cec_message
from .entity import crestron_device_info

_LOGGER = logging.getLogger(__name__)

# Backoff after a longpoll failure (auth expiry, network drop) so a broken
# device doesn't spin the loop and spam the log.
_ERROR_BACKOFF_SECONDS = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron NVX CEC event entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]

    entities = [
        CrestronNVXCecEvent(data["coordinators"][device.name], device)
        for device in api.devices.values()
        if device.is_transmitter and device.hdmi_inputs > 0
    ]
    async_add_entities(entities)


class CrestronNVXCecEvent(CoordinatorEntity, EventEntity):
    """Fires an HA event for each recognized CEC command from the source device."""

    _attr_should_poll = False
    _attr_event_types = CEC_EVENT_TYPES

    def __init__(self, coordinator, device):
        """Initialize the CEC event entity."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} CEC Command"
        self._attr_unique_id = f"{device.entity_id_prefix}_cec_command"
        self._attr_icon = "mdi:remote"
        self._attr_device_info = crestron_device_info(device)
        self._task: asyncio.Task | None = None

    async def async_added_to_hass(self) -> None:
        """Start the background CEC listener."""
        await super().async_added_to_hass()
        self._task = self.hass.async_create_background_task(
            self._listen(), name=f"crestron_nvx_cec_{self.device.host}"
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop the background CEC listener."""
        await super().async_will_remove_from_hass()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        """Long-poll the device forever, firing an event per recognized CEC command."""
        while True:
            try:
                changed = await self.device.longpoll(timeout=30)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the listener alive on any device hiccup
                _LOGGER.exception(
                    "CEC listener error for %s, retrying in %ss",
                    self.device.host,
                    _ERROR_BACKOFF_SECONDS,
                )
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                continue

            if not changed:
                continue  # longpoll timeout with no changes - go again immediately

            raw_message = self._extract_cec_message(changed)
            if raw_message is None:
                continue

            event_type = decode_cec_message(raw_message)
            if event_type is None:
                continue  # a CEC message we don't map to an event (e.g. key-release)

            self._trigger_event(event_type)
            self.async_write_ha_state()

    @staticmethod
    def _extract_cec_message(longpoll_data: dict) -> str | None:
        """Pull ReceiveCecMessage out of a Longpoll response, if present."""
        try:
            return longpoll_data["Device"]["AudioVideoInputOutput"]["Inputs"][0]["Ports"][0][
                "Hdmi"
            ]["ReceiveCecMessage"]
        except (KeyError, TypeError, IndexError):
            return None
