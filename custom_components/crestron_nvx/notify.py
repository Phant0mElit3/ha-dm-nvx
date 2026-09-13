"""Notify platform for Crestron NVX - push arbitrary text to a device's OSD.

Not receiver-only: some transmitter models (e.g. DM-NVX-352) have a local
HDMI output with its own OSD too, confirmed live - so this is gated on
device.osd_supported alone, not device role.

For automations that want to show a status message on-screen (e.g. "DSP
Mode: Movie" when switching sources or DSP modes): calling
notify.send_message on this entity sets the OSD text and turns the OSD on,
then automatically turns it off again a few seconds later. Sending another
message before that timer elapses cancels the pending turn-off and starts a
fresh one, so the new text stays on screen instead of flickering.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .crestron_nvx_api import CrestronNVXError
from .entity import crestron_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron NVX notify entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]

    entities = [
        CrestronNVXOsdNotify(data["coordinators"][device.name], device)
        for device in api.devices.values()
        if device.osd_supported
    ]
    async_add_entities(entities)


class CrestronNVXOsdNotify(CoordinatorEntity, NotifyEntity):
    """Sends a message to the device's OSD, auto-clearing it after a delay."""

    def __init__(self, coordinator, device):
        """Initialize the notify entity."""
        super().__init__(coordinator)
        self.device = device
        self._send_lock = asyncio.Lock()
        self._attr_name = f"{device.name} OSD"
        self._attr_unique_id = f"{device.entity_id_prefix}_osd_notify"
        self._attr_icon = "mdi:message-text-outline"
        self._attr_device_info = crestron_device_info(device)
        self._clear_task: asyncio.Task | None = None

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Show message on the OSD, replacing/extending any message already showing."""
        async with self._send_lock:
            await self._cancel_clear_task()
            try:
                if not await self.device.set_osd(text=message, enabled=True):
                    raise HomeAssistantError("The NVX device rejected the OSD message")
            except CrestronNVXError as err:
                raise HomeAssistantError(str(err)) from err
            finally:
                # A previous OSD may still be visible even when replacement fails.
                self._clear_task = self.hass.async_create_background_task(
                    self._clear_after_delay(), name=f"crestron_nvx_osd_clear_{self.device.host}"
                )

    async def _clear_after_delay(self) -> None:
        try:
            await asyncio.sleep(self.device.osd_display_seconds)
        except asyncio.CancelledError:
            raise
        else:
            try:
                if not await self.device.set_osd(enabled=False):
                    _LOGGER.error("Failed to clear OSD on %s", self.device.host)
            except CrestronNVXError:
                _LOGGER.warning("Could not clear OSD on %s", self.device.host)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending clear-OSD task."""
        pending = self._clear_task is not None and not self._clear_task.done()
        await self._cancel_clear_task()
        if pending:
            try:
                await self.device.set_osd(enabled=False)
            except CrestronNVXError:
                _LOGGER.warning("Could not clear OSD during unload for %s", self.device.host)
        await super().async_will_remove_from_hass()

    async def _cancel_clear_task(self) -> None:
        """Cancel and drain any pending OSD clear task."""
        if self._clear_task:
            self._clear_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._clear_task
            self._clear_task = None
