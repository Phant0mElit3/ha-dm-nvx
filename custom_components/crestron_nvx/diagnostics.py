"""Diagnostics support for the Crestron NVX integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "host",
    "name",
    "title",
    "unique_id",
    "serial_number",
    "entity_id_prefix",
    "ip_address",
    "mac_address",
    "StreamLocation",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    api = runtime_data.get("api")
    coordinators = runtime_data.get("coordinators", {})

    devices = []
    if api is not None:
        for device_name, device in api.devices.items():
            coordinator = coordinators.get(device_name)
            coordinator_data = coordinator.data if coordinator else {}
            devices.append(
                {
                    "name": device.name,
                    "host": device.host,
                    "model": device.model,
                    "serial_number": device.serial_number,
                    "firmware_version": device.firmware_version,
                    "device_mode": device.device_mode,
                    "hdmi_inputs": device.hdmi_inputs,
                    "hdmi_outputs": device.hdmi_outputs,
                    "osd_supported": device.osd_supported,
                    "preview_supported": device.preview_supported,
                    "identify_supported": device.identify_supported,
                    "test_patterns": device.test_patterns,
                    "latest_data_keys": sorted((coordinator_data or {}).keys()),
                    "last_update_success": coordinator.last_update_success if coordinator else None,
                    "status": {
                        key: value
                        for key, value in (coordinator_data or {}).items()
                        if key
                        in ("video", "ethernet", "identify", "test_pattern", "primary_stream")
                    },
                }
            )

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "devices": async_redact_data(devices, TO_REDACT),
    }
