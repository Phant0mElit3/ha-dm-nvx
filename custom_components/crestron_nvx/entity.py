"""Shared device_info builder for Crestron NVX entities."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .crestron_nvx_api import CrestronNVXError


async def async_run_command(coordinator, command) -> None:
    """Report rejected writes to the caller and reconcile from device state."""
    try:
        success = await command
    except CrestronNVXError as err:
        raise HomeAssistantError(str(err)) from err
    if not success:
        raise HomeAssistantError("The NVX device rejected the command")
    await coordinator.async_request_refresh()


def crestron_device_info(device) -> dict:
    """Build the HA device_info dict for a Crestron NVX device.

    Shows the real hardware model (e.g. "DM-NVX-E30"), not just its role, and
    a configuration_url pointing at the device's own web UI so it's obvious
    which physical unit an entity belongs to.
    """
    info = {
        "identifiers": {(DOMAIN, device.entity_id_prefix)},
        "name": device.name,
        "manufacturer": "Crestron",
        "model": device.model or f"NVX {device.device_mode}",
        "configuration_url": f"https://{device.host}",
    }
    if device.firmware_version:
        info["sw_version"] = device.firmware_version
    if device.serial_number:
        info["serial_number"] = device.serial_number
    return info
