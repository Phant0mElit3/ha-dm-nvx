"""The Crestron NVX integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL, DOMAIN
from .crestron_nvx_api import (
    CrestronNVXAPI,
    CrestronNVXAuthError,
    CrestronNVXConnectionError,
    CrestronNVXDevice,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.EVENT,
    Platform.SWITCH,
    Platform.NOTIFY,
    Platform.NUMBER,
    Platform.CAMERA,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Crestron NVX from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    devices_config = entry.data.get("devices", [])
    if not devices_config:
        raise ConfigEntryNotReady("No NVX devices configured")
    verify_ssl = devices_config[0].get(CONF_VERIFY_SSL, False) if devices_config else False
    session = async_create_clientsession(
        hass,
        verify_ssl=verify_ssl,
        auto_cleanup=False,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    api = CrestronNVXAPI(verify_ssl=verify_ssl, session=session)

    try:
        for device_config in devices_config:
            device = await api.add_device(
                host=device_config[CONF_HOST],
                name=device_config["name"],
                username=device_config[CONF_USERNAME],
                password=device_config[CONF_PASSWORD],
            )
            device.entity_id_prefix = device_config.get("entity_id_prefix", device.host)
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, 30)
        )
        coordinators: dict[str, CrestronNVXDataUpdateCoordinator] = {}
        for device_name, device in api.devices.items():
            coordinator = CrestronNVXDataUpdateCoordinator(
                hass, device=device, update_interval=timedelta(seconds=scan_interval), entry=entry
            )
            await coordinator.async_config_entry_first_refresh()
            coordinators[device_name] = coordinator
        configs = [
            {
                **config,
                "serial_number": api.devices[config["name"]].serial_number,
                "entity_id_prefix": config.get("entity_id_prefix", config[CONF_HOST]),
            }
            for config in devices_config
        ]
        if configs != devices_config:
            hass.config_entries.async_update_entry(entry, data={**entry.data, "devices": configs})
        hass.data[DOMAIN][entry.entry_id] = {
            "api": api,
            "session": session,
            "coordinators": coordinators,
        }
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException as err:
        if entry.entry_id in hass.data[DOMAIN]:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            hass.data[DOMAIN].pop(entry.entry_id, None)
        try:
            await api.close()
        finally:
            await session.close()
        if isinstance(err, CrestronNVXAuthError):
            raise ConfigEntryAuthFailed(str(err)) from err
        if isinstance(err, CrestronNVXConnectionError):
            raise ConfigEntryNotReady(str(err)) from err
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (e.g. the preview camera toggle)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()
        await data["session"].close()

    return unload_ok


class CrestronNVXDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetches video/ethernet/route status for a single NVX device."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: CrestronNVXDevice,
        update_interval: timedelta,
        entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.device = device
        super().__init__(
            hass,
            _LOGGER,
            name=f"Crestron NVX {device.host}",
            update_interval=update_interval,
            config_entry=entry,
        )

    async def _async_update_data(self) -> dict:
        """Fetch status from the device."""
        try:
            data = {
                "video": await self.device.get_video_status(),
                "ethernet": await self.device.get_ethernet_status(),
            }
            if data["video"] is None or data["ethernet"] is None:
                raise UpdateFailed("Device did not return video and Ethernet status")
            if self.device.hdmi_inputs > 0:
                data["device_specific"] = await self.device.get_device_specific()
            if self.device.test_patterns:
                data["test_pattern"] = await self.device.get_test_pattern()
            if self.device.identify_supported:
                data["identify"] = await self.device.get_identify()
            if self.device.is_receiver:
                data["discovered_streams"] = await self.device.get_discovered_streams()
                data["route"] = await self.device.get_current_route()
                data["primary_stream"] = await self.device.get_primary_stream()
                data["route_control"] = await self.device.get_route_control()
            return data
        except CrestronNVXAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CrestronNVXConnectionError as err:
            raise UpdateFailed(f"Error communicating with {self.device.host}: {err}") from err
